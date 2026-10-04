"""Motores de pronóstico usados en la industria, para competir con la LSTM producto a producto.

    Seasonal Naive          repetir la temporada anterior (referencia mínima)
    ETS, Theta, ARIMA       modelos estadísticos clásicos (statsforecast)
    Croston, TSB            demanda intermitente (productos con muchos períodos en cero)
    LightGBM                gradient boosting global con retardos, calendario y exógenas (enfoque M5)
    Combinación             promedio de los dos mejores motores del producto

Cada motor pronostica desde un corte de la historia (índice por entidad) un número de períodos y
entrega P10, P50 y P90. El torneo elige por producto el de menor error en el tramo de selección.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from .datos import FRECUENCIAS

TEMPORADA = {"D": 7, "W": 52, "M": 12, "Q": 4}
MAX_PUNTOS_ARIMA = 4_000          # AutoARIMA es lento: solo en datasets chicos
MAX_SERIES_ETS = 150
UMBRAL_INTERMITENTE = 0.30        # fracción de períodos en cero para considerar Croston y TSB
NIVEL = 80                        # intervalo 80% -> P10 y P90

LSTM = "LSTM"
COMBINACION = "Combinación"


def _ordenar(p10, p50, p90):
    p50 = np.maximum(np.asarray(p50, float), 0.0)
    p10 = np.minimum(np.maximum(np.asarray(p10, float), 0.0), p50)
    p90 = np.maximum(np.asarray(p90, float), p50)
    return {"P10": p10, "P50": p50, "P90": p90}


def intermitentes(series, ents) -> set:
    return {e for e in ents if (series[e].crudo[:, 0] <= 0).mean() >= UMBRAL_INTERMITENTE}


# ---------------------------------------------------------------- estadísticos (statsforecast)

def motores_estadisticos(series, ents, freq=None) -> list:
    """ARIMA se omite en datos semanales: con temporada 52 su búsqueda es demasiado lenta."""
    # con catálogos grandes ETS es el motor más lento (búsqueda de modelo por serie) y se omite
    nombres = ["Seasonal Naive", "Theta"] if len(ents) > MAX_SERIES_ETS else ["Seasonal Naive", "ETS", "Theta"]
    if sum(len(series[e].fechas) for e in ents) <= MAX_PUNTOS_ARIMA and freq != "W":
        nombres.append("ARIMA")
    if intermitentes(series, ents):
        nombres += ["Croston", "TSB"]
    return nombres


def estadisticos(series, ents, freq, hasta: dict, h: int, nombres: list) -> dict:
    """{motor: {entidad: {P10, P50, P90}}} pronosticando h períodos desde hasta[e] (excluido)."""
    from statsforecast import StatsForecast
    from statsforecast.models import (TSB, AutoARIMA, AutoETS, AutoTheta, CrostonOptimized,
                                      SeasonalNaive)
    from statsforecast.utils import ConformalIntervals

    filas = []
    for e in ents:
        s = series[e]
        k = hasta[e]
        filas.append(pd.DataFrame({"unique_id": str(e), "ds": s.fechas[:k], "y": s.crudo[:k, 0].astype(float)}))
    df = pd.concat(filas, ignore_index=True)
    largo_min = min(hasta[e] for e in ents)
    m = TEMPORADA[freq] if largo_min >= 2 * TEMPORADA[freq] + 2 else 1
    ci = ConformalIntervals(h=h, n_windows=2) if largo_min > 3 * h else None
    fabrica = {
        "Seasonal Naive": lambda: SeasonalNaive(m, alias="Seasonal Naive"),
        "ETS": lambda: AutoETS(season_length=m, alias="ETS"),
        "Theta": lambda: AutoTheta(season_length=m, alias="Theta"),
        "ARIMA": lambda: AutoARIMA(season_length=m, alias="ARIMA"),
        "Croston": lambda: CrostonOptimized(alias="Croston", prediction_intervals=ci),
        "TSB": lambda: TSB(0.1, 0.1, alias="TSB", prediction_intervals=ci),
    }
    usar = [n for n in nombres if n in fabrica and (ci is not None or n not in ("Croston", "TSB"))]
    if m == 1:   # sin temporada completa, Seasonal Naive repite el último valor: no compite
        usar = [n for n in usar if n != "Seasonal Naive"]
    out = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sf = StatsForecast(models=[fabrica[n]() for n in usar], freq=FRECUENCIAS[freq]["pandas"], n_jobs=1)
        fc = sf.forecast(df=df, h=h, level=[NIVEL])
    for n in usar:
        out[n] = {}
        for e in ents:
            g = fc[fc["unique_id"] == str(e)]
            if len(g) != h or g[n].isna().any():
                continue
            lo, hi = g.get(f"{n}-lo-{NIVEL}", g[n]), g.get(f"{n}-hi-{NIVEL}", g[n])
            out[n][e] = _ordenar(lo.fillna(g[n]).to_numpy(), g[n].to_numpy(), hi.fillna(g[n]).to_numpy())
    return out


# ---------------------------------------------------------------- LightGBM global

RETARDOS = {"D": [1, 2, 3, 4, 5, 6, 7, 14, 21, 28], "W": [1, 2, 3, 4, 8, 13, 26, 52], "M": [1, 2, 3, 6, 12],
            "Q": [1, 2, 3, 4]}
MEDIAS = {"D": [7, 28], "W": [4, 13], "M": [3, 6], "Q": [2, 4]}


def _calendario(fechas: pd.DatetimeIndex, freq: str) -> np.ndarray:
    f = pd.DatetimeIndex(fechas)
    cols = [f.month.to_numpy(), np.sin(2 * np.pi * f.dayofyear / 365.25), np.cos(2 * np.pi * f.dayofyear / 365.25)]
    if freq == "D":
        cols += [f.dayofweek.to_numpy(), f.day.to_numpy()]
    elif freq == "W":
        cols += [f.isocalendar().week.to_numpy().astype(float)]
    return np.column_stack(cols).astype(float)


class _LGBM:
    """Un modelo por cuantil, global para todas las entidades (la demanda se escala por la media de cada una)."""

    def __init__(self, freq, retardos, medias, n_exog):
        self.freq, self.retardos, self.medias, self.n_exog = freq, retardos, medias, n_exog

    def _fila(self, y_hist, cal, exog, ent_id):
        """y_hist: demanda escalada hasta el período anterior (al menos max retardo valores)."""
        lags = [y_hist[-r] for r in self.retardos]
        meds = [float(np.mean(y_hist[-w:])) for w in self.medias]
        return np.concatenate([[ent_id], lags, meds, cal, exog])

    def ajustar(self, series, ents, hasta, escala_y, escala_x):
        import lightgbm as lgb
        X, y = [], []
        inicio = max(max(self.retardos), max(self.medias))
        for i, e in enumerate(ents):
            s = series[e]
            ys = s.crudo[:hasta[e], 0] / escala_y[e]
            ex = s.crudo[:hasta[e], 1:] / escala_x[e]
            cal = _calendario(s.fechas[:hasta[e]], self.freq)
            for t in range(inicio, hasta[e]):
                X.append(self._fila(ys[:t], cal[t], ex[t], i))
                y.append(ys[t])
        X, y = np.asarray(X, float), np.asarray(y, float)
        if len(y) < 60:
            raise ValueError("historia insuficiente para LightGBM")
        chico = len(y) < 3_000
        base = dict(n_estimators=250 if chico else 500, learning_rate=0.05, num_leaves=15 if chico else 31,
                    min_child_samples=10 if chico else 20, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
                    random_state=42, verbose=-1, n_jobs=1)
        self.modelos = {}
        for q, a in (("P10", 0.1), ("P50", 0.5), ("P90", 0.9)):
            m = lgb.LGBMRegressor(objective="quantile", alpha=a, **base)
            m.fit(X, y, categorical_feature=[0])
            self.modelos[q] = m
        return self

    def pronosticar(self, series, ents, hasta, h, escala_y, escala_x, exog_futuras=None):
        hist = {e: list(series[e].crudo[:hasta[e], 0] / escala_y[e]) for e in ents}
        sal = {e: {q: np.zeros(h) for q in self.modelos} for e in ents}
        freq_pd = FRECUENCIAS[self.freq]["pandas"]
        fechas = {e: pd.date_range(series[e].fechas[hasta[e] - 1], periods=h + 1, freq=freq_pd)[1:] for e in ents}
        cal = {e: _calendario(fechas[e], self.freq) for e in ents}
        for k in range(h):
            filas = []
            for i, e in enumerate(ents):
                s = series[e]
                if exog_futuras is not None:
                    ex = exog_futuras[e][k]
                elif hasta[e] + k < len(s.fechas):
                    ex = s.crudo[hasta[e] + k, 1:]
                else:
                    ex = s.crudo[-1, 1:]
                filas.append(self._fila(np.asarray(hist[e]), cal[e][k], np.asarray(ex, float) / escala_x[e], i))
            X = np.asarray(filas, float)
            pred = {q: m.predict(X) for q, m in self.modelos.items()}
            for i, e in enumerate(ents):
                for q in pred:
                    sal[e][q][k] = pred[q][i]
                hist[e].append(pred["P50"][i])
        return {e: _ordenar(sal[e]["P10"] * escala_y[e], sal[e]["P50"] * escala_y[e], sal[e]["P90"] * escala_y[e])
                for e in ents}


def lightgbm(series, ents, freq, hasta: dict, h: int, exog_futuras=None) -> dict:
    """{entidad: {P10, P50, P90}} con un LightGBM global entrenado con la historia hasta hasta[e]."""
    minimo = min(hasta[e] for e in ents)
    retardos = [r for r in RETARDOS[freq] if r <= minimo // 3] or [1]
    medias = [w for w in MEDIAS[freq] if w <= minimo // 3] or [1]
    escala_y = {e: max(float(np.mean(series[e].crudo[:hasta[e], 0])), 1e-6) for e in ents}
    escala_x = {}
    for e in ents:
        ex = series[e].crudo[:hasta[e], 1:]
        escala_x[e] = np.where(np.abs(ex).mean(axis=0) > 0, np.abs(ex).mean(axis=0), 1.0) if ex.shape[1] else np.ones(0)
    n_exog = series[ents[0]].crudo.shape[1] - 1
    mod = _LGBM(freq, retardos, medias, n_exog).ajustar(series, ents, hasta, escala_y, escala_x)
    return mod.pronosticar(series, ents, hasta, h, escala_y, escala_x, exog_futuras)


# ---------------------------------------------------------------- torneo

def wape(real, pred) -> float:
    real, pred = np.asarray(real, float), np.asarray(pred, float)
    return float(np.abs(real - pred).sum() / max(real.sum(), 1e-9) * 100)


def combinar(a: dict, b: dict) -> dict:
    return _ordenar((a["P10"] + b["P10"]) / 2, (a["P50"] + b["P50"]) / 2, (a["P90"] + b["P90"]) / 2)


def factor_banda(p: dict, real, objetivo=0.80) -> float:
    """Ensancha la banda P10-P90 alrededor del P50 hasta que cubra al menos el 80% de lo real en el
    tramo de selección (calibración conformal simple)."""
    real = np.asarray(real, float)
    abajo, arriba = p["P50"] - p["P10"], p["P90"] - p["P50"]
    for k in np.arange(1.0, 4.01, 0.05):
        dentro = (real >= p["P50"] - k * abajo) & (real <= p["P50"] + k * arriba)
        if dentro.mean() >= objetivo:
            return float(k)
    return 4.0


def escalar_banda(p: dict, k: float) -> dict:
    return _ordenar(p["P50"] - k * (p["P50"] - p["P10"]), p["P50"], p["P50"] + k * (p["P90"] - p["P50"]))


def elegir(candidatos: dict, real: np.ndarray, permitidos=None) -> tuple:
    """candidatos {motor: {P10,P50,P90}} en el tramo de selección. Agrega la combinación de los dos mejores y
    devuelve (ganador, {motor: wape}, (motor_a, motor_b) de la combinación)."""
    errores = {m: wape(real, p["P50"]) for m, p in candidatos.items() if permitidos is None or m in permitidos}
    orden = sorted(errores, key=errores.get)
    par = None
    if len(orden) >= 2:
        par = (orden[0], orden[1])
        errores[COMBINACION] = wape(real, combinar(candidatos[par[0]], candidatos[par[1]])["P50"])
    ganador = min(errores, key=errores.get)
    return ganador, errores, par

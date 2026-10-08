"""Clima como señal de corto plazo (Open-Meteo: gratuito, sin clave).

Los motores pronostican la demanda normal (sin clima). Encima, una capa de corto plazo ajusta los próximos días
según cuánto se aleja el pronóstico del tiempo de lo normal para esa época, con el efecto medido en el historial
de cada producto ("demand sensing"). Más allá del pronóstico del tiempo (16 días) no hay ajuste.

- Historial: archivo de reanálisis (ERA5), con unos 5 días de atraso.
- Días recientes y próximos 16 días: API de pronóstico.
- Más allá: clima típico de esa fecha (solo para completar la serie; no genera ajuste).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import requests

URL_GEO = "https://geocoding-api.open-meteo.com/v1/search"
URL_HISTORIAL = "https://archive-api.open-meteo.com/v1/archive"
URL_PRONOSTICO = "https://api.open-meteo.com/v1/forecast"
DIARIAS = "temperature_2m_max,temperature_2m_min,precipitation_sum"
VARIABLES = ("temperatura", "lluvia")
ATRASO_HISTORIAL = 6        # días que tarda el archivo en tener datos definitivos
DIAS_PRONOSTICO = 16
TIMEOUT = 30


def buscar(nombre: str, n: int = 6) -> list[dict]:
    """Lugares que coinciden con el nombre: dict(nombre, lat, lon)."""
    r = requests.get(URL_GEO, params={"name": nombre, "count": n, "language": "es", "format": "json"}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for x in r.json().get("results") or []:
        partes = [x.get("name"), x.get("admin1"), x.get("country")]
        out.append({"nombre": ", ".join(p for p in partes if p), "lat": float(x["latitude"]),
                    "lon": float(x["longitude"])})
    return out


def _diario(js) -> pd.DataFrame:
    d = js.get("daily") or {}
    if not d.get("time"):
        return pd.DataFrame(columns=["fecha", *VARIABLES])
    tmax = np.array(d.get("temperature_2m_max"), dtype=float)
    tmin = np.array(d.get("temperature_2m_min"), dtype=float)
    return pd.DataFrame({"fecha": pd.to_datetime(d["time"]), "temperatura": (tmax + tmin) / 2,
                         "lluvia": np.array(d.get("precipitation_sum"), dtype=float)})


def historial(lat, lon, desde, hasta) -> pd.DataFrame:
    r = requests.get(URL_HISTORIAL, params={"latitude": lat, "longitude": lon, "start_date": f"{desde:%Y-%m-%d}",
                                            "end_date": f"{hasta:%Y-%m-%d}", "daily": DIARIAS, "timezone": "auto"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    return _diario(r.json())


def pronostico(lat, lon) -> pd.DataFrame:
    r = requests.get(URL_PRONOSTICO, params={"latitude": lat, "longitude": lon, "daily": DIARIAS, "timezone": "auto",
                                             "past_days": 10, "forecast_days": DIAS_PRONOSTICO}, timeout=TIMEOUT)
    r.raise_for_status()
    return _diario(r.json())


def serie_diaria(lat, lon, desde, hasta, hoy=None) -> pd.DataFrame:
    """Clima diario de desde a hasta (DataFrame indexado por fecha). El pasado es observado; lo que aún no ocurre
    viene del pronóstico (16 días) y, más allá, del clima típico de esa fecha."""
    desde, hasta = pd.Timestamp(desde).normalize(), pd.Timestamp(hasta).normalize()
    hoy = pd.Timestamp(hoy or pd.Timestamp.today()).normalize()
    fin_historial = min(hasta, hoy - pd.Timedelta(days=ATRASO_HISTORIAL))
    partes = []
    if fin_historial >= desde:
        partes.append(historial(lat, lon, desde, fin_historial))
    if hasta > fin_historial:
        try:
            partes.append(pronostico(lat, lon))
        except requests.RequestException:
            pass        # sin pronóstico se usa el clima típico
    obs = pd.concat(partes) if partes else pd.DataFrame(columns=["fecha", *VARIABLES])
    obs = obs.dropna(subset=["temperatura"]).drop_duplicates("fecha", keep="first").set_index("fecha").sort_index()
    idx = pd.date_range(desde, hasta, freq="D")
    serie = obs.reindex(idx)
    tipico_dia = serie["temperatura"].isna().to_numpy()
    # clima típico: promedio del mismo día del año; si falta, el del mes; si falta, el promedio general
    if len(obs):
        dia = obs.groupby([obs.index.month, obs.index.day]).mean()
        mes = obs.groupby(obs.index.month).mean()
        for v in VARIABLES:
            faltan = serie[v].isna()
            if faltan.any():
                f = serie.index[faltan]
                tipico = [dia[v].get((m, d_), np.nan) for m, d_ in zip(f.month, f.day)]
                tipico = np.where(np.isnan(tipico), mes[v].reindex(f.month).to_numpy(), tipico)
                serie.loc[faltan, v] = np.where(np.isnan(tipico), obs[v].mean(), tipico)
    serie.index.name = "fecha"
    serie = serie.astype(float)
    serie["tipico"] = tipico_dia          # True: no es dato observado ni pronosticado
    return serie


def por_periodo(diaria: pd.DataFrame, fechas, frecuencia: str) -> pd.DataFrame:
    """Clima de cada período del dataset: el día (D), los 7 días que terminan en la fecha (W), el mes o el
    trimestre que empieza en la fecha (M, Q). La columna tipico es la fracción de días sin dato real."""
    fechas = pd.DatetimeIndex(pd.to_datetime(fechas)).normalize()
    d = diaria[list(VARIABLES) + (["tipico"] if "tipico" in diaria else [])].astype(float)
    if frecuencia == "D":
        base, claves = d, fechas
    elif frecuencia == "W":
        base, claves = d.rolling(7, min_periods=1).mean(), fechas
    else:
        base = d.resample("MS" if frecuencia == "M" else "QS").mean()
        claves = fechas.to_period("M" if frecuencia == "M" else "Q").to_timestamp()
    out = base.reindex(claves).ffill().bfill()
    out.index = fechas
    return out


VENTANA_ANOMALIA = {"D": 28, "W": 8, "M": 3, "Q": 2}
MIN_PERIODOS = {"D": 120, "W": 40, "M": 18, "Q": 10}


def anomalias(tabla: pd.DataFrame, frecuencia: str) -> pd.DataFrame:
    """Cuánto se aleja el clima de su nivel de esas semanas (media móvil centrada)."""
    w = VENTANA_ANOMALIA.get(frecuencia, 8)
    return tabla[list(VARIABLES)] - tabla[list(VARIABLES)].rolling(w, center=True, min_periods=max(2, w // 2)).mean()


def _anomalia_demanda(g: pd.DataFrame, frecuencia: str) -> pd.Series:
    w = VENTANA_ANOMALIA.get(frecuencia, 8)
    y = g["objetivo"].astype(float)
    nivel = y.rolling(w, center=True, min_periods=max(2, w // 2)).mean()
    ya = y / nivel.replace(0, np.nan) - 1
    if frecuencia == "D":
        ya = ya - ya.groupby(g["fecha"].dt.dayofweek.to_numpy()).transform("mean")
    return ya


def estimar_efecto(df: pd.DataFrame, diaria: pd.DataFrame, frecuencia: str, hasta=None) -> pd.DataFrame:
    """Efecto del clima en cada entidad: regresión de la desviación de la demanda (respecto de su nivel de esas
    semanas) sobre la desviación de la temperatura y de la lluvia. efecto_pct = % de demanda por °C o por mm.
    relevante: el coeficiente es claramente distinto de cero (|t| >= 2,5) con historia suficiente.
    hasta: usa solo datos hasta esa fecha (para medirlo sin mirar el período de prueba)."""
    fechas = pd.DatetimeIndex(sorted(df["fecha"].unique()))
    anom = anomalias(por_periodo(diaria, fechas, frecuencia), frecuencia)
    filas = []
    for ent, g in df.sort_values("fecha").groupby("entidad"):
        if hasta is not None:
            g = g[g["fecha"] < pd.Timestamp(hasta)]
        ya = _anomalia_demanda(g, frecuencia).to_numpy()
        X = anom.reindex(pd.DatetimeIndex(g["fecha"])).to_numpy()
        ok = np.isfinite(ya) & np.isfinite(X).all(axis=1)
        n = int(ok.sum())
        if n < MIN_PERIODOS.get(frecuencia, 40):
            continue
        Xo, yo = X[ok], ya[ok]
        usable = Xo.std(axis=0) > 1e-9
        if not usable.any():
            continue
        Xu = Xo[:, usable]
        coef, *_ = np.linalg.lstsq(Xu, yo, rcond=None)
        resid = yo - Xu @ coef
        s2 = resid.var(ddof=Xu.shape[1]) if n > Xu.shape[1] else np.nan
        try:
            ee = np.sqrt(np.diag(s2 * np.linalg.inv(Xu.T @ Xu)))
        except np.linalg.LinAlgError:
            continue
        j = 0
        for k, v in enumerate(VARIABLES):
            if not usable[k]:
                continue
            t = coef[j] / ee[j] if ee[j] > 0 else 0.0
            filas.append({"entidad": ent, "variable": v, "efecto_pct": float(coef[j] * 100), "t": float(t), "n": n,
                          "relevante": bool(abs(t) >= 2.5)})
            j += 1
    return pd.DataFrame(filas, columns=["entidad", "variable", "efecto_pct", "t", "n", "relevante"])


def factores(efecto: pd.DataFrame, diaria: pd.DataFrame, fechas_hist, fechas_fut, frecuencia: str) -> dict:
    """Multiplicador de la demanda por entidad para las fechas futuras, según cuánto se aleja el pronóstico del
    tiempo de lo normal. Donde el clima es solo el típico (más allá del pronóstico del tiempo) el factor es 1."""
    if efecto is None or not len(efecto):
        return {}
    efecto = efecto[efecto["usa"]] if "usa" in efecto else efecto[efecto["relevante"]]
    if not len(efecto):
        return {}
    todas = pd.DatetimeIndex(sorted(set(pd.to_datetime(fechas_hist)) | set(pd.to_datetime(fechas_fut))))
    tabla = por_periodo(diaria, todas, frecuencia)
    anom = anomalias(tabla, frecuencia).reindex(pd.to_datetime(fechas_fut))
    real = (tabla.get("tipico", pd.Series(0.0, index=tabla.index)).reindex(pd.to_datetime(fechas_fut)) < 0.5)
    out = {}
    for ent, g in efecto[efecto["relevante"]].groupby("entidad"):
        f = np.ones(len(anom))
        for v, e in zip(g["variable"], g["efecto_pct"]):
            f = f + e / 100 * np.nan_to_num(anom[v].to_numpy())
        f = np.where(real.to_numpy(), np.clip(f, 0.3, 3.0), 1.0)
        out[ent] = f
    return out


def ajustar(pron: dict, fac: dict) -> dict:
    """Aplica los factores de clima a un pronóstico {entidad: DataFrame(fecha, P10, P50, P90)}."""
    if not fac:
        return pron
    out = dict(pron)
    for e, f in fac.items():
        if e in out:
            d = out[e].copy()
            n = min(len(d), len(f))
            for q in ("P10", "P50", "P90"):
                if q in d:
                    d.loc[d.index[:n], q] = d[q].to_numpy()[:n] * f[:n]
            out[e] = d
    return out


def _wape(real, pred):
    real, pred = np.asarray(real, float), np.asarray(pred, float)
    den = np.abs(real).sum()
    return float(np.abs(real - pred).sum() / den * 100) if den > 0 else np.nan


MEJORA_MINIMA = 0.02        # la capa se usa si baja el error al menos 2 % (relativo)


def validar(df: pd.DataFrame, diaria: pd.DataFrame, frecuencia: str, backtest: dict) -> pd.DataFrame:
    """Efecto del clima por entidad con la prueba que decide si se usa: el efecto se mide solo con datos anteriores
    a la prueba con datos pasados y se aplica a ese pronóstico. usa = relevante y bajó el error.
    Agrega wape_sin y wape_con (el efecto final se mide con toda la historia)."""
    final = estimar_efecto(df, diaria, frecuencia)
    if not len(final):
        return final.assign(usa=False, wape_sin=np.nan, wape_con=np.nan)
    final["usa"], final["wape_sin"], final["wape_con"] = False, np.nan, np.nan
    fechas_hist = pd.DatetimeIndex(sorted(df["fecha"].unique()))
    for ent in final.loc[final["relevante"], "entidad"].unique():
        bt = backtest.get(ent)
        if bt is None or not len(bt):
            continue
        inicio = pd.Timestamp(bt["fecha"].iloc[0])
        previo = estimar_efecto(df[df["entidad"] == ent], diaria, frecuencia, hasta=inicio)
        if not len(previo) or not previo["relevante"].any():
            continue
        previo = previo.assign(usa=previo["relevante"])
        f = factores(previo, diaria, fechas_hist[fechas_hist < inicio], bt["fecha"], frecuencia).get(ent)
        if f is None:
            continue
        sin, con = _wape(bt["real"], bt["P50"]), _wape(bt["real"], bt["P50"].to_numpy() * f)
        fila = final["entidad"] == ent
        final.loc[fila, ["wape_sin", "wape_con"]] = sin, con
        final.loc[fila, "usa"] = final.loc[fila, "relevante"] & (con < sin * (1 - MEJORA_MINIMA))
    return final

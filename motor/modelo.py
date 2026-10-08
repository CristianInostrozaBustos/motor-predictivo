"""Motor de modelo: construcción, búsqueda automática de ventana, entrenamiento,
evaluación y pronóstico recursivo.

Cada serie se divide en tres tramos consecutivos:

    [ entrenamiento ........ | selección (V) | prueba (V) ]

1. Búsqueda: para cada ventana candidata se entrena con el tramo de entrenamiento y se
   pronostica de forma recursiva el tramo de selección. Gana la de menor error (WAPE).
2. Modelo final: con la ventana ganadora se entrena con entrenamiento + selección y se
   pronostica de forma recursiva el tramo de prueba, que nunca se usó para decidir nada.
   Esas son las métricas que ve el usuario.
3. Pronóstico futuro: recursivo desde el final del historial.

La entrada del modelo son las variables del dataset (objetivo + exógenas) escaladas por
entidad, más variables de calendario (seno y coseno del día de la semana, del año, etc.),
que se conocen hacia el futuro y ayudan con la estacionalidad.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers
from tensorflow.keras.saving import register_keras_serializable

from .datos import FRECUENCIAS, DatasetPreparado
from .reglas import PlanEntrenamiento

SEMILLA = 42
EPOCAS_BUSQUEDA, PACIENCIA_BUSQUEDA = 15, 3
EPOCAS_FINAL, PACIENCIA_FINAL = 80, 8
MAX_MUESTRAS_BUSQUEDA = 8_000
TAM_LOTE = 256
TASA_APRENDIZAJE = 2e-3
DIM_EMBEDDING = 8
FRAC_PARADA = 0.15   # último tramo de las ventanas de entrenamiento usado para la parada temprana


def fijar_semilla(s=SEMILLA):
    keras.utils.set_random_seed(s)


# ---------------------------------------------------------------- calendario

def calendario(fechas: pd.DatetimeIndex, freq: str) -> np.ndarray:
    f = pd.DatetimeIndex(fechas)
    cols = []

    def ciclo(valor, periodo):
        a = 2 * np.pi * np.asarray(valor, dtype=float) / periodo
        cols.extend([np.sin(a), np.cos(a)])

    if freq == "D":
        ciclo(f.dayofweek, 7)
        ciclo(f.dayofyear, 365.25)
    elif freq == "W":
        ciclo(f.isocalendar().week.to_numpy(), 52.18)
    elif freq == "M":
        ciclo(f.month - 1, 12)
    else:
        ciclo(f.quarter - 1, 4)
    return np.column_stack(cols).astype("float32")


# ---------------------------------------------------------------- escalado

class EscaladorEntidad:
    """Min-Max por entidad y variable, ajustado solo con los datos de entrenamiento."""

    def __init__(self):
        self.min, self.rango = {}, {}

    def ajustar(self, ent, M):
        mn, mx = np.nanmin(M, axis=0), np.nanmax(M, axis=0)
        r = mx - mn
        r[r == 0] = 1.0
        self.min[ent], self.rango[ent] = mn.astype("float32"), r.astype("float32")

    def transformar(self, ent, M):
        return ((M - self.min[ent]) / self.rango[ent]).astype("float32")

    def objetivo_a_escala(self, ent, v):
        return (np.asarray(v, dtype="float32") - self.min[ent][0]) / self.rango[ent][0]

    def objetivo_desde_escala(self, ent, v):
        return np.asarray(v, dtype="float64") * self.rango[ent][0] + self.min[ent][0]


# ---------------------------------------------------------------- arquitectura

@register_keras_serializable(package="motor")
class SeleccionarPorEntidad(layers.Layer):
    def call(self, inputs):
        valores, entidad_id = inputs
        return tf.gather(valores, tf.cast(entidad_id, tf.int32), batch_dims=1)


def _cuantiles(x, s=""):
    p50 = layers.Dense(1, name=f"p50{s}")(x)
    d90 = layers.Dense(1, activation="softplus", name=f"d90{s}")(x)
    d10 = layers.Dense(1, activation="softplus", name=f"d10{s}")(x)
    return p50, layers.Add(name=f"p90{s}")([p50, d90]), layers.Subtract(name=f"p10{s}")([p50, d10])


def construir(esquema, arq, ventana, n_entrada, n_entidades):
    serie = layers.Input(shape=(ventana, n_entrada), name="serie")
    ent = layers.Input(shape=(1,), dtype="int32", name="entidad")
    x = serie
    if esquema == "embedding":
        e = layers.Flatten()(layers.Embedding(n_entidades, DIM_EMBEDDING, name="embedding_entidad")(ent))
        x = layers.Concatenate(axis=-1)([serie, layers.RepeatVector(ventana)(e)])
    u1, u2 = arq["lstm"]
    x = layers.LSTM(u1, return_sequences=True)(x)
    x = layers.Dropout(arq["dropout"])(x)
    x = layers.LSTM(u2)(x)
    x = layers.Dropout(arq["dropout"])(x)
    x = layers.Dense(arq["dense_tronco"], activation="relu", name="tronco")(x)

    if esquema == "cabezas":
        p50s, p90s, p10s = [], [], []
        for i in range(n_entidades):
            h = layers.Dense(arq["dense_cabeza"], activation="relu", name=f"cabeza_{i}")(x)
            a, b, c = _cuantiles(h, f"_{i}")
            p50s.append(a); p90s.append(b); p10s.append(c)
        cat = lambda L, n: layers.Concatenate(axis=1, name=n)(L)
        salidas = {
            "P50": SeleccionarPorEntidad(name="P50")([cat(p50s, "p50s"), ent]),
            "P90": SeleccionarPorEntidad(name="P90")([cat(p90s, "p90s"), ent]),
            "P10": SeleccionarPorEntidad(name="P10")([cat(p10s, "p10s"), ent]),
        }
    else:
        h = layers.Dense(arq["dense_cabeza"], activation="relu", name="cabeza")(x)
        a, b, c = _cuantiles(h)
        salidas = {"P50": layers.Identity(name="P50")(a), "P90": layers.Identity(name="P90")(b),
                   "P10": layers.Identity(name="P10")(c)}
    return keras.Model(inputs={"serie": serie, "entidad": ent}, outputs=salidas)


def _pinball(q):
    def perdida(y, yp):
        e = y - yp
        return tf.reduce_mean(tf.maximum(q * e, (q - 1) * e))
    return perdida


def compilar(m):
    m.compile(optimizer=keras.optimizers.Adam(TASA_APRENDIZAJE),
              loss={"P50": _pinball(0.5), "P90": _pinball(0.9), "P10": _pinball(0.1)})


# ---------------------------------------------------------------- datos por entidad

@dataclass
class Serie:
    """Una entidad lista para el modelo."""
    fechas: pd.DatetimeIndex
    crudo: np.ndarray        # (n, n_vars) sin escalar: objetivo + exógenas
    cal: np.ndarray          # (n, n_cal) calendario


def series_por_entidad(dp: DatasetPreparado, entidades) -> dict:
    out = {}
    for e, g in dp.df[dp.df["entidad"].isin(entidades)].groupby("entidad", sort=True):
        f = pd.DatetimeIndex(g["fecha"])
        out[e] = Serie(f, g[dp.variables_modelo].to_numpy(dtype="float32"), calendario(f, dp.config.frecuencia))
    return out


def _entrada(esc, ent, s: Serie, desde, hasta, crudo=None):
    """Matriz de entrada (escalada + calendario) para las filas [desde, hasta)."""
    C = s.crudo if crudo is None else crudo
    return np.concatenate([esc.transformar(ent, C[desde:hasta]), s.cal[desde:hasta]], axis=1)


def ventanas_entrenamiento(series, esc, ent_a_id, ventana, fin_por_ent):
    """Ventanas deslizantes hasta fin (excluido). El último FRAC_PARADA de cada entidad va a parada temprana."""
    X, e, y, Xp, ep, yp = [], [], [], [], [], []
    for ent, s in series.items():
        fin = fin_por_ent[ent]
        Z = _entrada(esc, ent, s, 0, fin)
        idx = list(range(ventana, fin))
        n_p = max(1, int(len(idx) * FRAC_PARADA))
        for j, i in enumerate(idx):
            destino = (Xp, ep, yp) if j >= len(idx) - n_p else (X, e, y)
            destino[0].append(Z[i - ventana:i]); destino[1].append(ent_a_id[ent]); destino[2].append(Z[i, 0])
    a = lambda L, dt="float32": np.asarray(L, dtype=dt)
    return (a(X), a(e, "int32").reshape(-1, 1), a(y), a(Xp), a(ep, "int32").reshape(-1, 1), a(yp))


def _entrenar(modelo, datos, epocas, paciencia, al_epoca=None, max_muestras=None):
    X, e, y, Xp, ep, yp = datos
    if max_muestras and len(X) > max_muestras:
        sel = np.sort(np.random.default_rng(SEMILLA).choice(len(X), max_muestras, replace=False))
        X, e, y = X[sel], e[sel], y[sel]
    cbs = [keras.callbacks.EarlyStopping(monitor="val_loss", patience=paciencia, restore_best_weights=True),
           keras.callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=max(2, paciencia // 2), min_lr=1e-5)]
    if al_epoca:
        cbs.append(keras.callbacks.LambdaCallback(on_epoch_end=lambda ep_, logs: al_epoca(ep_ + 1, epocas)))
    h = modelo.fit({"serie": X, "entidad": e}, {"P50": y, "P90": y, "P10": y},
                   validation_data=({"serie": Xp, "entidad": ep}, {"P50": yp, "P90": yp, "P10": yp}),
                   epochs=epocas, batch_size=TAM_LOTE, verbose=0, callbacks=cbs, shuffle=True)
    return h.history


# ---------------------------------------------------------------- pronóstico recursivo (común)

def _inferir(modelo, W, ids):
    """Predicción compilada (mucho más rápida que model.predict para lotes chicos repetidos)."""
    fn = getattr(modelo, "_fn_inferencia", None)
    if fn is None:
        @tf.autograph.experimental.do_not_convert
        def _f(serie, entidad):
            return modelo({"serie": serie, "entidad": entidad}, training=False)
        fn = tf.function(_f, reduce_retracing=True)
        modelo._fn_inferencia = fn
    return fn(tf.constant(W, dtype=tf.float32), tf.constant(ids, dtype=tf.int32))


def recursivo(modelo, esc, ent_a_id, ventana, contextos, exog_futuras, cal_futuro, pasos):
    """Pronóstico recursivo de `pasos` períodos para varias entidades a la vez.

    contextos[ent]: matriz (ventana, n_entrada) ya escalada con calendario.
    exog_futuras[ent]: (pasos, n_vars-1) sin escalar. cal_futuro[ent]: (pasos, n_cal).
    Devuelve dict ent -> {"P10","P50","P90"} en unidades reales.
    """
    ents = list(contextos)
    W = np.stack([contextos[e] for e in ents]).astype("float32")
    ids = np.array([ent_a_id[e] for e in ents], dtype="int32").reshape(-1, 1)
    sal = {q: np.zeros((len(ents), pasos), dtype="float32") for q in ("P10", "P50", "P90")}
    for h in range(pasos):
        pred = {q: v.numpy() for q, v in _inferir(modelo, W, ids).items()}
        for q in sal:
            sal[q][:, h] = pred[q].reshape(-1)
        nuevas = []
        for k, e in enumerate(ents):
            crudo = np.concatenate([[0.0], exog_futuras[e][h]]).astype("float32")[None, :]
            fila = esc.transformar(e, crudo)[0]
            fila[0] = pred["P50"][k, 0]
            nuevas.append(np.concatenate([fila, cal_futuro[e][h]]))
        W = np.concatenate([W[:, 1:, :], np.stack(nuevas)[:, None, :]], axis=1)
    out = {}
    for k, e in enumerate(ents):
        p = {q: np.maximum(0.0, esc.objetivo_desde_escala(e, sal[q][k])) for q in sal}
        out[e] = {"P10": np.minimum(p["P10"], p["P50"]), "P50": p["P50"], "P90": np.maximum(p["P90"], p["P50"])}
    return out


def _evaluar_tramo(modelo, esc, ent_a_id, ventana, series, inicio_por_ent, largo):
    """Pronóstico recursivo de un tramo del historial, usando las exógenas reales de ese tramo."""
    ctx, exf, calf = {}, {}, {}
    for e, s in series.items():
        i0 = inicio_por_ent[e]
        ctx[e] = _entrada(esc, e, s, i0 - ventana, i0)
        exf[e] = s.crudo[i0:i0 + largo, 1:]
        calf[e] = s.cal[i0:i0 + largo]
    return recursivo(modelo, esc, ent_a_id, ventana, ctx, exf, calf, largo)


# ---------------------------------------------------------------- métricas

def wape(real, pred):
    real, pred = np.asarray(real, float), np.asarray(pred, float)
    if real.sum() <= 0:     # sin demanda en el tramo: el error relativo no está definido
        return np.nan
    return float(np.abs(real - pred).sum() / real.sum() * 100)


def mape(real, pred):
    real, pred = np.asarray(real, float), np.asarray(pred, float)
    m = real != 0
    return float(np.mean(np.abs((real[m] - pred[m]) / real[m])) * 100) if m.any() else np.nan


def cobertura(real, p10, p90):
    real = np.asarray(real, float)
    return float(((real >= p10) & (real <= p90)).mean() * 100)


def naive_estacional(s: Serie, i0, largo, periodo):
    """Repetir el valor de la temporada anterior (o el último, si no hay historia suficiente)."""
    y = s.crudo[:, 0]
    lag = periodo if i0 - periodo >= 0 else 1
    # se repite la última temporada conocida antes del tramo (sin mirar datos del tramo)
    return np.array([y[i - lag * ((i - i0) // lag + 1)] for i in range(i0, i0 + largo)])


# ---------------------------------------------------------------- orquestación

@dataclass
class ResultadoModelo:
    modelo: keras.Model
    plan: PlanEntrenamiento
    ventana: int
    escalador: EscaladorEntidad
    ent_a_id: dict
    series: dict                     # ent -> Serie (historial completo, para pronosticar)
    exog_nombres: list
    exog_ultimo: dict                # ent -> dict var -> último valor
    busqueda: pd.DataFrame
    metricas_entidad: pd.DataFrame   # entidad, wape, mape, cobertura, wape_naive, mape_1paso
    backtest: dict                   # ent -> DataFrame fecha, real, P10, P50, P90, naive
    historial_perdida: dict
    epocas: int
    segundos: float
    notas: list = field(default_factory=list)
    torneo: pd.DataFrame | None = None          # entidad, motor, wape_seleccion, wape_prueba, elegido
    motor_por_entidad: dict | None = None       # entidad -> motor ganador
    futuro_torneo: dict | None = None           # entidad -> DataFrame(fecha, P10, P50, P90) hasta el horizonte máximo
    factor_banda: dict | None = None            # entidad -> factor de calibración de la banda P10-P90
    picos: dict | None = None                   # entidad -> cantidad de picos suavizados para entrenar


def entrenar_motor(dp: DatasetPreparado, plan: PlanEntrenamiento, al_avance=None) -> ResultadoModelo:
    """al_avance(fraccion 0..1, texto) para mostrar progreso en el sitio."""
    t0 = time.time()
    avisar = al_avance or (lambda f, s: None)
    V = plan.validacion
    ents = plan.entidades_incluidas
    ent_a_id = {e: i for i, e in enumerate(ents)}
    series = series_por_entidad(dp, ents)
    n_vars = len(dp.variables_modelo)
    n_entrada = n_vars + next(iter(series.values())).cal.shape[1]
    fi = FRECUENCIAS[plan.frecuencia]
    largo = {e: len(s.fechas) for e, s in series.items()}
    ini_sel = {e: n - 2 * V for e, n in largo.items()}
    ini_prueba = {e: n - V for e, n in largo.items()}
    picos = _picos_por_entidad(dp, series) if dp.config.suavizar_picos else {}
    # los picos se suavizan para entrenar y elegir; el tramo de prueba conserva lo real hasta medir el error
    _suavizar(series, picos, lambda e, i: i < ini_prueba[e])

    # ---------------- 1) búsqueda de ventana
    candidatas = list(plan.ventanas)
    peso = 0.4 if len(candidatas) > 1 else 0.1
    filas, seleccion_lstm = [], {}
    # la búsqueda corre siempre: deja el pronóstico de la LSTM en el tramo de selección para el torneo
    esc_b = EscaladorEntidad()
    for e, s in series.items():
        esc_b.ajustar(e, s.crudo[:ini_sel[e]])
    for k, v in enumerate(candidatas):
        base = peso * k / len(candidatas)
        texto = f"Buscando la mejor configuración ({k + 1} de {len(candidatas)})"
        avisar(base, texto)
        fijar_semilla()
        m = construir(plan.esquema, plan.arquitectura, v, n_entrada, len(ents))
        compilar(m)
        ts = time.time()
        h = _entrenar(m, ventanas_entrenamiento(series, esc_b, ent_a_id, v, ini_sel), EPOCAS_BUSQUEDA,
                      PACIENCIA_BUSQUEDA, max_muestras=MAX_MUESTRAS_BUSQUEDA,
                      al_epoca=lambda ep, tot, b=base, t=texto: avisar(b + peso / len(candidatas) * ep / tot, t))
        pr = _evaluar_tramo(m, esc_b, ent_a_id, v, series, ini_sel, V)
        seleccion_lstm[v] = pr
        real = np.concatenate([series[e].crudo[ini_sel[e]:ini_sel[e] + V, 0] for e in ents])
        p50 = np.concatenate([pr[e]["P50"] for e in ents])
        p10 = np.concatenate([pr[e]["P10"] for e in ents])
        p90 = np.concatenate([pr[e]["P90"] for e in ents])
        filas.append(dict(ventana=v, wape=wape(real, p50), cobertura=cobertura(real, p10, p90),
                          epocas=len(h["loss"]), segundos=round(time.time() - ts, 1)))
        del m
        keras.backend.clear_session()
    busqueda = pd.DataFrame(filas)
    mejor = busqueda["wape"].min()
    # a igualdad práctica de error (menos de 2% relativo), gana la ventana más corta (modelo más simple)
    if np.isnan(mejor):     # sin demanda en el tramo de selección: la más corta
        ventana = int(busqueda["ventana"].min())
    else:
        ventana = int(busqueda[busqueda["wape"] <= mejor * 1.02].sort_values("ventana").iloc[0]["ventana"])
    busqueda["elegida"] = busqueda["ventana"] == ventana

    # ---------------- 2) modelo final (entrenamiento + selección) y prueba
    texto = f"Entrenando el modelo final (ventana de {ventana} {fi['unidad_pl']})"
    avisar(peso, texto)
    fijar_semilla()
    esc = EscaladorEntidad()
    for e, s in series.items():
        esc.ajustar(e, s.crudo[:ini_prueba[e]])
    modelo = construir(plan.esquema, plan.arquitectura, ventana, n_entrada, len(ents))
    compilar(modelo)
    hist = _entrenar(modelo, ventanas_entrenamiento(series, esc, ent_a_id, ventana, ini_prueba), EPOCAS_FINAL,
                     PACIENCIA_FINAL, al_epoca=lambda ep, tot: avisar(peso + (0.78 - peso) * ep / tot, texto))

    avisar(0.79, "Midiendo la precisión")
    pr = _evaluar_tramo(modelo, esc, ent_a_id, ventana, series, ini_prueba, V)
    # un paso adelante (con datos reales en la ventana), comparable con la validación clásica
    X1, e1 = [], []
    for e, s in series.items():
        Z = _entrada(esc, e, s, 0, largo[e])
        for i in range(ini_prueba[e], largo[e]):
            X1.append(Z[i - ventana:i]); e1.append(ent_a_id[e])
    p1 = modelo.predict({"serie": np.asarray(X1, "float32"), "entidad": np.asarray(e1, "int32").reshape(-1, 1)},
                        verbose=0, batch_size=2048)["P50"].reshape(-1)

    periodo = fi["periodo_estacional"]
    backtest, filas_m = {}, []
    for k, e in enumerate(ents):
        s = series[e]
        i0 = ini_prueba[e]
        real = s.crudo[i0:i0 + V, 0].astype(float)
        nv = naive_estacional(s, i0, V, periodo)
        uno = np.maximum(0, esc.objetivo_desde_escala(e, p1[k * V:(k + 1) * V]))
        backtest[e] = pd.DataFrame({"fecha": s.fechas[i0:i0 + V], "real": real, "P10": pr[e]["P10"],
                                    "P50": pr[e]["P50"], "P90": pr[e]["P90"], "naive": nv})
        filas_m.append(dict(entidad=e, wape=wape(real, pr[e]["P50"]), mape=mape(real, pr[e]["P50"]),
                            cobertura=cobertura(real, pr[e]["P10"], pr[e]["P90"]), wape_naive=wape(real, nv),
                            mape_1paso=mape(real, uno)))
    metricas = pd.DataFrame(filas_m)

    exog_nombres = dp.variables_modelo[1:]
    exog_ultimo = {e: {v: float(series[e].crudo[-1, j + 1]) for j, v in enumerate(exog_nombres)} for e in ents}
    res = ResultadoModelo(modelo=modelo, plan=plan, ventana=ventana, escalador=esc, ent_a_id=ent_a_id,
                          series=series, exog_nombres=exog_nombres, exog_ultimo=exog_ultimo, busqueda=busqueda,
                          metricas_entidad=metricas, backtest=backtest, historial_perdida=hist,
                          epocas=len(hist["loss"]), segundos=0.0)
    res.picos = {e: len(v) for e, v in picos.items()}
    res.clima_diaria, res.clima_freq = getattr(dp, "clima_diaria", None), dp.config.frecuencia
    avisar(0.8, "Comparando con otros modelos de pronóstico")
    _torneo(res, ini_sel, ini_prueba, seleccion_lstm[ventana], avisar,
            lambda: _suavizar(series, picos, lambda e, i: i >= ini_prueba[e]))
    res.segundos = round(time.time() - t0, 1)
    avisar(1.0, "Listo")
    return res


# ---------------------------------------------------------------- torneo de motores

def _picos_por_entidad(dp: DatasetPreparado, series) -> dict:
    """{entidad: [(posición, valor típico), ...]} de los picos detectados en la preparación."""
    out = {}
    for e, g in dp.picos.groupby("entidad"):
        if e not in series:
            continue
        pos = {f: i for i, f in enumerate(series[e].fechas)}
        out[e] = [(pos[f], t) for f, t in zip(pd.DatetimeIndex(g["fecha"]), g["tipico"]) if f in pos]
    return out


def _suavizar(series, picos, condicion):
    for e, lista in picos.items():
        for i, tipico in lista:
            if condicion(e, i):
                series[e].crudo[i, 0] = tipico


def _torneo(res: ResultadoModelo, ini_sel, ini_prueba, seleccion_lstm, avisar, antes_del_futuro=None):
    """Compite la LSTM con los motores de la industria producto a producto (tramo de selección) y deja en el
    resultado la prueba y el pronóstico futuro del ganador de cada uno."""
    from . import motores as MT
    plan, series, V = res.plan, res.series, res.plan.validacion
    ents, freq = list(series), plan.frecuencia
    intermit = MT.intermitentes(series, ents)
    nombres = MT.motores_estadisticos(series, ents, freq)
    sel, prueba = {MT.LSTM: seleccion_lstm}, {MT.LSTM: {e: {q: res.backtest[e][q].to_numpy() for q in
                                                            ("P10", "P50", "P90")} for e in ents}}
    for nombre_corte, corte, destino, avance in (("seleccion", ini_sel, sel, 0.84), ("prueba", ini_prueba, prueba, 0.9)):
        try:
            destino.update(MT.estadisticos(series, ents, freq, corte, V, nombres))
        except Exception:  # noqa: BLE001
            pass
        try:
            destino["LightGBM"] = MT.lightgbm(series, ents, freq, corte, V)
        except Exception:  # noqa: BLE001
            pass
        avisar(avance, "Comparando con otros modelos de pronóstico")

    def de(dic, motor, e, par):
        if motor == MT.COMBINACION:
            a, b = de(dic, par[0], e, None), de(dic, par[1], e, None)
            return MT.combinar(a, b) if a is not None and b is not None else None
        return dic.get(motor, {}).get(e)

    filas, ganadores, pares, k_banda = [], {}, {}, {}
    for e in ents:
        real_sel = series[e].crudo[ini_sel[e]:ini_sel[e] + V, 0]
        real_pr = series[e].crudo[ini_prueba[e]:ini_prueba[e] + V, 0]
        cand = {m: d[e] for m, d in sel.items() if e in d and (m not in ("Croston", "TSB") or e in intermit)}
        cand = {m: p for m, p in cand.items() if m == MT.LSTM or e in prueba.get(m, {})}
        ganador, errores, par = MT.elegir(cand, real_sel)
        ganadores[e], pares[e] = ganador, par
        for m, w in errores.items():
            p = de(prueba, m, e, par)
            filas.append(dict(entidad=e, motor=m, wape_seleccion=w,
                              wape_prueba=MT.wape(real_pr, p["P50"]) if p is not None else np.nan, elegido=m == ganador))
        k_banda[e] = MT.factor_banda(de(sel, ganador, e, par), real_sel)
        p = MT.escalar_banda(de(prueba, ganador, e, par), k_banda[e])
        bt = res.backtest[e]
        for q in ("P10", "P50", "P90"):
            bt[q] = p[q]
        fila_m = res.metricas_entidad["entidad"] == e
        res.metricas_entidad.loc[fila_m, "wape"] = wape(real_pr, p["P50"])
        res.metricas_entidad.loc[fila_m, "mape"] = mape(real_pr, p["P50"])
        res.metricas_entidad.loc[fila_m, "cobertura"] = cobertura(real_pr, p["P10"], p["P90"])
        if ganador != MT.LSTM:
            res.metricas_entidad.loc[fila_m, "mape_1paso"] = np.nan

    if antes_del_futuro:
        antes_del_futuro()
    # pronóstico futuro de los ganadores que no son la LSTM (con toda la historia)
    h = plan.horizonte_max
    necesarios = {m for e in ents for m in ((pares[e] or ()) if ganadores[e] == MT.COMBINACION else (ganadores[e],))}
    fin = {e: len(series[e].fechas) for e in ents}
    futuro = {}
    estad = [m for m in necesarios if m in nombres]
    if estad:
        try:
            futuro.update(MT.estadisticos(series, ents, freq, fin, h, estad))
        except Exception:  # noqa: BLE001  (si falla, esos productos quedan con la LSTM)
            pass
    if "LightGBM" in necesarios:
        fq = FRECUENCIAS[freq]["pandas"]
        exf = {e: _exogenas_futuras(res, e, pd.date_range(series[e].fechas[-1], periods=h + 1, freq=fq)[1:])
               for e in ents}
        try:
            futuro["LightGBM"] = MT.lightgbm(series, ents, freq, fin, h, exf)
        except Exception:  # noqa: BLE001
            pass
    if MT.LSTM in necesarios:
        lstm = pronosticar(res, h, freq, solo_lstm=True)
        futuro[MT.LSTM] = {e: {q: lstm[e][q].to_numpy() for q in ("P10", "P50", "P90")} for e in ents}
    res.futuro_torneo = {}
    for e in ents:
        if ganadores[e] == MT.LSTM:
            continue
        p = de(futuro, ganadores[e], e, pares[e])
        if p is None:
            ganadores[e] = MT.LSTM
            continue
        p = MT.escalar_banda(p, k_banda[e])
        fechas = pd.date_range(series[e].fechas[-1], periods=h + 1, freq=FRECUENCIAS[freq]["pandas"])[1:]
        res.futuro_torneo[e] = pd.DataFrame({"fecha": fechas, **p})
    res.motor_por_entidad = ganadores
    res.factor_banda = k_banda
    res.torneo = pd.DataFrame(filas)


# ---------------------------------------------------------------- pronóstico futuro

def _freq_de(fechas) -> str:
    paso = (fechas[1] - fechas[0]).days if len(fechas) > 1 else 1
    return "D" if paso <= 1 else "W" if paso <= 7 else "M" if paso <= 31 else "Q"


def _exogenas_futuras(res: ResultadoModelo, e, fechas_fut):
    """Supuestos hacia el futuro: precio = último valor, promoción = 0, clima = pronóstico y luego clima típico,
    otras variables = valor de hace un año (o el último si no hay)."""
    s = res.series[e]
    serie_hist = pd.DataFrame(s.crudo[:, 1:], index=s.fechas, columns=res.exog_nombres)
    clima_fut = None
    if getattr(res, "clima_diaria", None) is not None:
        from . import clima as C
        clima_fut = C.por_periodo(res.clima_diaria, fechas_fut, getattr(res, "clima_freq", None) or _freq_de(fechas_fut))
    cols = []
    for v in res.exog_nombres:
        if clima_fut is not None and v in clima_fut.columns:
            cols.append(clima_fut[v].to_numpy(dtype=float))
        elif v == "precio":
            cols.append(np.full(len(fechas_fut), res.exog_ultimo[e][v]))
        elif v == "promocion":
            cols.append(np.zeros(len(fechas_fut)))
        else:
            antes = serie_hist[v].reindex(fechas_fut - pd.DateOffset(years=1)).to_numpy(dtype=float)
            cols.append(np.where(np.isnan(antes), res.exog_ultimo[e][v], antes))
    return np.column_stack(cols).astype("float32") if cols else np.zeros((len(fechas_fut), 0), "float32")


def pronosticar(res: ResultadoModelo, horizonte: int, freq: str, cambios=None, entidades=None,
                solo_lstm: bool = False) -> dict:
    """dict entidad -> DataFrame(fecha, P10, P50, P90) con `horizonte` períodos desde el fin del historial.

    cambios (opcional, para escenarios): lista de dicts con
        var: nombre de la variable exógena del modelo ("precio", "promocion", u otra)
        desde, hasta: índices de período dentro del horizonte (hasta excluido)
        tipo: "pct" (multiplica por 1 + valor), "sumar" (suma valor) o "fijar" (reemplaza por valor)
        valor: número
    entidades (opcional): solo pronostica esas entidades.
    Cada entidad usa el motor que ganó el torneo. Los escenarios con cambios en exógenas se aplican como la razón
    entre la LSTM con y sin cambios (los motores estadísticos no usan exógenas).
    """
    fq = FRECUENCIAS[freq]["pandas"]
    ctx, exf, calf, fechas = {}, {}, {}, {}
    for e, s in res.series.items():
        if entidades is not None and e not in entidades:
            continue
        n = len(s.fechas)
        ctx[e] = _entrada(res.escalador, e, s, n - res.ventana, n)
        ff = pd.date_range(s.fechas[-1], periods=horizonte + 1, freq=fq)[1:]
        fechas[e] = ff
        ex = _exogenas_futuras(res, e, ff)
        for c in cambios or []:
            if c["var"] not in res.exog_nombres:
                continue
            j = res.exog_nombres.index(c["var"])
            sl = slice(max(0, c["desde"]), min(horizonte, c["hasta"]))
            if c["tipo"] == "pct":
                ex[sl, j] = ex[sl, j] * (1 + c["valor"])
            elif c["tipo"] == "sumar":
                ex[sl, j] = ex[sl, j] + c["valor"]
            else:
                ex[sl, j] = c["valor"]
        exf[e] = ex
        calf[e] = calendario(ff, freq)
    pr = recursivo(res.modelo, res.escalador, res.ent_a_id, res.ventana, ctx, exf, calf, horizonte)
    out = {e: pd.DataFrame({"fecha": fechas[e], "P10": pr[e]["P10"], "P50": pr[e]["P50"], "P90": pr[e]["P90"]})
           for e in ctx}
    futuro = getattr(res, "futuro_torneo", None) or {}
    k_banda = getattr(res, "factor_banda", None) or {}
    if solo_lstm or getattr(res, "motor_por_entidad", None) is None:
        return out
    from . import motores as MT
    for e in out:
        if e not in futuro and e in k_banda:
            p = MT.escalar_banda({q: out[e][q].to_numpy() for q in ("P10", "P50", "P90")}, k_banda[e])
            out[e] = pd.DataFrame({"fecha": out[e]["fecha"], **p})
    otros = [e for e in out if e in futuro]
    base_lstm = pronosticar(res, horizonte, freq, entidades=otros, solo_lstm=True) if (cambios and otros) else None
    for e in otros:
        f = futuro[e].iloc[:horizonte].reset_index(drop=True)
        if len(f) < horizonte:
            f = pd.concat([f, out[e].iloc[len(f):].reset_index(drop=True)], ignore_index=True)
        if base_lstm is not None:
            razon = (out[e]["P50"] / base_lstm[e]["P50"].replace(0, np.nan)).fillna(1.0).clip(0, 10).to_numpy()
            f[["P10", "P50", "P90"]] = f[["P10", "P50", "P90"]].to_numpy() * razon[:, None]
        f["fecha"] = out[e]["fecha"].to_numpy()
        out[e] = f[["fecha", "P10", "P50", "P90"]]
    return out

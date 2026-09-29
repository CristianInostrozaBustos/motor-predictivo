"""Del pronóstico a la decisión de abastecimiento.

Sistema de revisión periódica con nivel meta (T) y punto de reorden (ROP) como alarma,
calculado con la demanda y la incertidumbre del pronóstico (no con el histórico):

    SS  = Z · σ · √L
    ROP = d · L + SS
    T   = d · (L + P) + SS

d y σ se toman de los períodos futuros que cubre el pedido (L + P). σ sale del ancho del
rango P10–P90: en una normal, P90 − P10 = 2 · 1,2816 · σ.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .datos import FRECUENCIAS

Z_NIVEL = {"80%": 0.842, "85%": 1.036, "90%": 1.282, "95%": 1.645, "97,5%": 1.960, "99%": 2.326}
Z_P10_P90 = 1.2816


@dataclass
class Parametros:
    lead_time_dias: float
    revision_dias: float
    nivel_servicio: str
    inventario_actual: float | None


@dataclass
class Decision:
    entidad: str
    d: float                 # demanda media por período en la cobertura L+P
    sigma: float             # desviación por período
    L: float                 # lead time en períodos
    P: float                 # revisión en períodos
    ss: float
    rop: float
    meta: float
    inventario: float | None
    estado: str              # "Pedir ahora" / "Riesgo de quiebre" / "Stock suficiente" / "Sin inventario"
    cantidad: float
    periodos_hasta_pedido: float | None
    cobertura_dias: float | None
    fecha_pedido: pd.Timestamp | None
    aviso: str = ""
    z: float = 1.282


def _suma(x: np.ndarray, desde: int, largo: float) -> float:
    """Suma de x desde `desde` durante `largo` períodos (admite fracción: 35,4 períodos = 35 completos + 0,4 del
    siguiente). Si el pronóstico se acaba, los períodos que faltan se completan con el promedio de la ventana."""
    n = len(x)
    enteros = int(np.floor(largo))
    frac = largo - enteros
    tramo = x[desde:min(n, desde + enteros + (1 if frac > 0 else 0))]
    if len(tramo) == 0:
        tramo = x[-1:]
    pesos = np.ones(len(tramo))
    if frac > 0 and len(tramo) == enteros + 1:
        pesos[-1] = frac
    total = float((tramo * pesos).sum())
    faltan = largo - pesos.sum()
    if faltan > 1e-9:
        total += faltan * float(tramo.mean())
    return total


def decidir(entidad: str, pron: pd.DataFrame, freq: str, par: Parametros) -> Decision:
    dias = FRECUENCIAS[freq]["dias"]
    L = max(par.lead_time_dias / dias, 1e-6)
    P = max(par.revision_dias / dias, 1e-6)
    cubre = int(np.ceil(L + P))
    ventana = pron.iloc[:max(1, min(cubre, len(pron)))]
    aviso = ""
    if cubre > len(pron):
        aviso = (f"El lead time más la revisión cubren {cubre} {FRECUENCIAS[freq]['unidad_pl']} y el pronóstico llega a "
                 f"{len(pron)}. Se usó el promedio disponible; alarga el horizonte para mayor precisión.")
    d = float(ventana["P50"].mean())
    sigma = float(((ventana["P90"] - ventana["P10"]) / (2 * Z_P10_P90)).clip(lower=0).mean())
    z = Z_NIVEL[par.nivel_servicio]
    ss = z * sigma * np.sqrt(L)
    p50 = pron["P50"].to_numpy(float)
    # el punto de reorden cubre la demanda pronosticada mientras llega el pedido (no un promedio)
    rop = _suma(p50, 0, L) + ss
    meta = _suma(p50, 0, L + P) + ss

    I = par.inventario_actual
    if I is None or np.isnan(I):
        return Decision(entidad, d, sigma, L, P, ss, rop, meta, None, "Sin inventario", 0.0, None, None, None, aviso, z)

    cobertura = I / d * dias if d > 0 else np.inf
    # fecha y cantidad del próximo pedido: salen de la misma simulación que se grafica, para que la tabla,
    # el texto y el gráfico digan lo mismo (el punto de reorden de ese día se recalcula con la demanda de entonces)
    from .escenarios import simular as simular_base
    rop_t, meta_t, _ = politica_dinamica(pron, L, P, z)
    sim = simular_base(pron["fecha"].to_numpy(), p50, I, rop_t, meta_t, L)
    ped = sim.index[sim["pedido"] > 0]
    if len(ped):
        k = int(ped[0])
        hasta, fecha, cantidad = float(k), pron["fecha"].iloc[k], float(sim["pedido"].iloc[k])
    else:
        hasta, fecha, cantidad = None, None, 0.0
    if I <= ss:
        estado = "Riesgo de quiebre"
    elif I <= rop or (hasta is not None and hasta == 0):
        estado = "Pedir ahora"
    else:
        estado = "Stock suficiente"
    if estado != "Stock suficiente" and (hasta is None or hasta > 0):
        hasta, fecha, cantidad = 0.0, pron["fecha"].iloc[0], max(0.0, meta - I)
    return Decision(entidad, d, sigma, L, P, ss, rop, meta, I, estado, cantidad, hasta, cobertura, fecha, aviso, z)


def politica_dinamica(pron: pd.DataFrame, L, P: float, z: float):
    """ROP, meta y SS recalculados en cada período con el pronóstico de los períodos que vienen.

    L puede ser un escalar o un arreglo (lead time en períodos del pedido emitido en cada período).
    Devuelve tres arreglos del largo del pronóstico.
    """
    n = len(pron)
    L = np.broadcast_to(np.asarray(L, float), (n,))
    p50 = pron["P50"].to_numpy(float)
    sig = ((pron["P90"] - pron["P10"]).to_numpy(float) / (2 * Z_P10_P90)).clip(min=0)
    rop, meta, ss = np.zeros(n), np.zeros(n), np.zeros(n)
    for t in range(n):
        # en el período t la demanda de t ya ocurrió: se cubre desde t+1 hasta que llegue el pedido
        w = max(1, int(np.ceil(L[t] + P)))
        sl = slice(min(t + 1, n - 1), min(n, t + 1 + w))
        sg = sig[sl].mean()
        ss[t] = z * sg * np.sqrt(L[t])
        rop[t] = _suma(p50, t + 1, L[t]) + ss[t]
        meta[t] = _suma(p50, t + 1, L[t] + P) + ss[t]
    return rop, meta, ss


def simular(pron: pd.DataFrame, dec: Decision, manuales: dict | None = None, auto_desde: int = 0) -> pd.DataFrame:
    """Inventario proyectado aplicando la política sobre el P50, recalculada período a período.

    Cada vez que la posición de inventario (disponible + en tránsito) cae bajo el ROP vigente se pide
    hasta la meta vigente; el pedido llega L períodos después. Venta perdida: el inventario no baja de cero.
    """
    if dec.inventario is None:
        return pd.DataFrame()
    from .escenarios import simular as simular_base
    rop, meta, ss = politica_dinamica(pron, dec.L, dec.P, dec.z)
    sim = simular_base(pron["fecha"].to_numpy(), pron["P50"].to_numpy(), dec.inventario, rop, meta, dec.L,
                       manuales=manuales, auto_desde=auto_desde)
    sim["ss"] = ss
    return sim


def evaluar_plan(sug: pd.DataFrame, tuyo: pd.DataFrame, hasta: int, precio=None, costo=None, dias_periodo=1.0) -> dict:
    """Compara la sugerencia del modelo con el plan del usuario.

    `hasta` es el período desde el que el modelo vuelve a comprar solo (el tramo que decidió el usuario es
    [0, hasta)). Devuelve unidades, gasto, ahorro, pérdidas y cuándo se normaliza la operación.
    Los montos solo se calculan si hay precio (ventas) o costo (compras).
    """
    n = len(sug)
    r = {}
    tramo = slice(0, min(hasta, n))
    r["compra_sug"] = float(sug["pedido"].iloc[tramo].sum())
    r["compra_tuya"] = float(tuyo["pedido"].iloc[tramo].sum())
    r["compra_total_sug"] = float(sug["pedido"].sum())
    r["compra_total_tuya"] = float(tuyo["pedido"].sum())
    r["quiebres_sug"] = int(sug["quiebre"].sum())
    r["quiebres_tuyo"] = int(tuyo["quiebre"].sum())
    r["perdida_sug"] = float(sug["no_atendida"].sum())
    r["perdida_tuya"] = float(tuyo["no_atendida"].sum())
    r["perdida_extra"] = max(0.0, r["perdida_tuya"] - r["perdida_sug"])
    r["inv_prom_sug"] = float(sug["inventario"].mean())
    r["inv_prom_tuyo"] = float(tuyo["inventario"].mean())
    q = np.where(tuyo["quiebre"].to_numpy())[0]
    r["primer_quiebre"] = tuyo["fecha"].iloc[q[0]] if len(q) else None
    r["ultimo_quiebre"] = tuyo["fecha"].iloc[q[-1]] if len(q) else None
    # la operación se normaliza cuando ya no hay quiebres y el inventario vuelve a parecerse al sugerido
    ref = max(float(sug["inventario"].max()), 1.0)
    cerca = np.abs(tuyo["inventario"].to_numpy() - sug["inventario"].to_numpy()) <= 0.05 * ref
    desde = int(q[-1]) + 1 if len(q) else min(hasta, n - 1)
    idx = next((t for t in range(desde, n) if cerca[t:].all()), None)
    r["normaliza"] = tuyo["fecha"].iloc[idx] if idx is not None else None
    r["dias_normaliza"] = (idx * dias_periodo) if idx is not None else None
    if costo:
        r["gasto_sug"] = r["compra_sug"] * costo
        r["gasto_tuyo"] = r["compra_tuya"] * costo
        r["ahorro_caja"] = r["gasto_sug"] - r["gasto_tuyo"]
    if precio:
        r["ventas_perdidas"] = r["perdida_extra"] * precio
        demanda_dia = float(sug["demanda"].mean()) / dias_periodo
        if costo and precio > costo:
            r["margen_perdido"] = r["perdida_extra"] * (precio - costo)
            margen_dia = demanda_dia * (precio - costo)
            r["dias_absorber"] = r["margen_perdido"] / margen_dia if margen_dia > 0 else None
        else:
            ingreso_dia = demanda_dia * precio
            r["dias_absorber"] = r["ventas_perdidas"] / ingreso_dia if ingreso_dia > 0 else None
    return r


def lead_time_dataset(serie_lead: pd.Series) -> float:
    """Lead time representativo: promedio de los últimos 90 registros."""
    return float(serie_lead.dropna().tail(90).mean())

"""Escenarios what-if y simulación de inventario.

Un escenario es un conjunto de eventos que ocurren durante una ventana de tiempo dentro
del horizonte. Hay dos tipos de efecto:

- Por el modelo: cambios en variables que el modelo usa como entrada (precio, promoción u
  otras exógenas). El pronóstico se recalcula completo con esos valores, así que el efecto
  en la demanda es el que el modelo aprendió de la historia.
- Directos: cambios que no pasan por el modelo. Un shock de demanda (+/- %) se aplica sobre
  el pronóstico, y un retraso del proveedor alarga el lead time de los pedidos emitidos
  durante el evento.

Para cada entidad se comparan tres mundos sobre el mismo horizonte:

    base            demanda base,      política actual
    sin ajustar     demanda escenario, política actual      -> lo que pasaría si no reaccionas
    ajustada        demanda escenario, política recalculada -> lo que pasaría si reaccionas a tiempo
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .datos import FRECUENCIAS
from .politica import Z_NIVEL, Decision, Parametros, decidir, politica_dinamica

VARIABLES_CLIMA = ("temperatura", "lluvia")

TIPOS = {
    "demanda": "Sube o baja la demanda",
    "retraso": "Retraso del proveedor",
    "precio": "Cambio de precio",
    "promocion": "Promoción",
    "exogena": "Cambio en otra variable",
}


@dataclass
class Evento:
    tipo: str              # clave de TIPOS
    valor: float           # % (como fracción) para demanda/precio/exógena; días para retraso; ignorado en promoción
    var: str | None = None  # variable exógena (solo tipo "exogena")
    elasticidad: float | None = None  # solo precio: si se da, el efecto se calcula con esta elasticidad y no con el modelo
    costo_alt: float | None = None    # solo retraso: costo unitario del proveedor alternativo (si se quiere evaluar cubrir con él)


@dataclass
class Escenario:
    eventos: list
    desde: int             # índice del primer período afectado dentro del horizonte
    duracion: int          # períodos

    @property
    def hasta(self):
        return self.desde + self.duracion

    def cambios_modelo(self) -> list:
        c = []
        for ev in self.eventos:
            if ev.tipo == "precio" and ev.elasticidad is None:
                c.append(dict(var="precio", desde=self.desde, hasta=self.hasta, tipo="pct", valor=ev.valor))
            elif ev.tipo == "promocion":
                c.append(dict(var="promocion", desde=self.desde, hasta=self.hasta, tipo="fijar", valor=1.0))
            elif ev.tipo == "exogena" and ev.var and ev.var not in VARIABLES_CLIMA:
                c.append(dict(var=ev.var, desde=self.desde, hasta=self.hasta, tipo="pct", valor=ev.valor))
        return c

    def factor_clima(self, efecto, entidad) -> float:
        """Multiplicador de la demanda por los eventos de clima (°C o mm), con el efecto medido en el historial."""
        f = 1.0
        if efecto is None or not len(efecto):
            return f
        for ev in self.eventos:
            if ev.tipo == "exogena" and ev.var in VARIABLES_CLIMA:
                fila = efecto[(efecto["entidad"] == entidad) & (efecto["variable"] == ev.var) & efecto["relevante"]]
                if len(fila):
                    f *= max(0.0, 1 + float(fila["efecto_pct"].iloc[0]) / 100 * ev.valor)
        return f

    def shock_demanda(self) -> float:
        k = sum(ev.valor for ev in self.eventos if ev.tipo == "demanda")
        k += sum(ev.elasticidad * ev.valor for ev in self.eventos if ev.tipo == "precio" and ev.elasticidad is not None)
        return k

    def cambio_precio(self) -> float:
        """Cambio % (fracción) del precio de venta durante el evento."""
        return sum(ev.valor for ev in self.eventos if ev.tipo == "precio")

    def costo_alternativo(self):
        for ev in self.eventos:
            if ev.tipo == "retraso" and ev.costo_alt:
                return float(ev.costo_alt)
        return None

    def retraso_dias(self) -> float:
        return sum(ev.valor for ev in self.eventos if ev.tipo == "retraso")

    def clave(self) -> tuple:
        return (tuple((e.tipo, round(e.valor, 6), e.var, e.elasticidad, e.costo_alt) for e in self.eventos),
                self.desde, self.duracion)


def aplicar_shock(pron: pd.DataFrame, esc: Escenario) -> pd.DataFrame:
    out = pron.copy()
    k = esc.shock_demanda()
    if k:
        sl = slice(esc.desde, esc.hasta)
        for q in ("P10", "P50", "P90"):
            out.loc[out.index[sl], q] = out[q].iloc[sl] * (1 + k)
    return out


def lead_time_por_periodo(lt_dias: float, esc: Escenario, n: int, freq: str) -> np.ndarray:
    """Lead time (en períodos) que tendría un pedido emitido en cada período."""
    dias = FRECUENCIAS[freq]["dias"]
    lt = np.full(n, lt_dias / dias)
    lt[esc.desde:esc.hasta] += esc.retraso_dias() / dias
    return lt


# ---------------------------------------------------------------- simulación

def simular(fechas, demanda, inventario0, rop, meta, lt_periodos, plan=None, manuales=None,
            auto_desde: int = 0) -> pd.DataFrame:
    """Simulación período a período con venta perdida (el inventario no baja de cero).

    rop, meta y lt_periodos pueden ser escalares o arreglos por período. Cuando la posición
    de inventario (disponible + en tránsito) llega al ROP se pide hasta la meta; el pedido
    llega después del lead time vigente al momento de pedir.

    `plan` es la demanda que el planificador espera (por defecto, la misma demanda). Se usa para mirar un
    período adelante: si esperar hasta mañana deja la posición bajo el punto de reorden de mañana, se pide hoy.
    Sin esto, con revisión diaria el pedido sale cuando la posición ya está hasta un día de demanda bajo el
    punto de reorden, y si el stock de seguridad es menor que un día de demanda se produce un quiebre.

    `manuales` son compras decididas por el usuario: {período de emisión: (cantidad, lead time en períodos)}.
    Las compras automáticas solo ocurren desde el período `auto_desde` (antes, decide el usuario).
    """
    n = len(demanda)
    rop = np.broadcast_to(np.asarray(rop, float), (n,))
    meta = np.broadcast_to(np.asarray(meta, float), (n,))
    lt = np.broadcast_to(np.asarray(lt_periodos, float), (n,))
    plan = np.asarray(demanda if plan is None else plan, float)
    manuales = manuales or {}
    I = float(inventario0)
    transito = []
    filas = []
    for t in range(n):
        llega = sum(q for k, q in transito if k == t)
        transito = [(k, q) for k, q in transito if k != t]
        I += llega
        d = float(demanda[t])
        atendida = min(I, d)
        perdida = d - atendida
        I -= atendida
        posicion = I + sum(q for _, q in transito)
        pedido = 0.0
        manual = 0.0
        if t in manuales:
            q, lt_m = manuales[t]
            if q > 0:
                manual = float(q)
                transito.append((t + max(1, int(np.ceil(lt_m))), manual))
                posicion += manual
        elif t >= auto_desde:
            manana = t + 1 < n and posicion - plan[t + 1] < rop[t + 1]
            if posicion <= rop[t] or manana:
                pedido = max(0.0, meta[t] - posicion)
                if pedido > 0:
                    transito.append((t + max(1, int(np.ceil(lt[t]))), pedido))
                    posicion += pedido
        filas.append(dict(fecha=fechas[t], demanda=d, inventario=I, llegada=llega, pedido=pedido + manual,
                          pedido_manual=manual, no_atendida=perdida, quiebre=perdida > 1e-9, rop=rop[t],
                          meta=meta[t], posicion=posicion))
    return pd.DataFrame(filas)


@dataclass
class Comparacion:
    entidad: str
    base: pd.DataFrame            # pronóstico base
    escenario: pd.DataFrame       # pronóstico con el escenario
    dec_base: Decision            # política estática (para cuando no hay inventario)
    dec_esc: Decision
    sim_base: pd.DataFrame | None
    sim_sin_ajuste: pd.DataFrame | None
    sim_ajustada: pd.DataFrame | None
    inicio_ajuste: int = 0
    resumen: dict = field(default_factory=dict)
    sim_alternativo: pd.DataFrame | None = None   # cubrir el retraso con el proveedor alternativo (sin retraso)


def _anticipar(L, k):
    """Lead time que planifica quien sabe lo que viene: el máximo de los próximos k períodos."""
    n = len(L)
    return np.array([L[t:min(n, t + k)].max() for t in range(n)])


def comparar(entidad, pron_base, pron_esc_modelo, par: Parametros, esc: Escenario, freq) -> Comparacion:
    """Compara tres mundos sobre el mismo horizonte, con la política recalculada período a período:

    base         demanda sin evento, lead time normal, política con el pronóstico base
    sin ajustar  demanda y lead time del evento, pero la política sigue usando el pronóstico base
    ajustada     demanda y lead time del evento, y la política usa el pronóstico del escenario y
                 anticipa el mayor lead time (planifica con el lead time que tendrá el próximo pedido)
    """
    pron_esc = aplicar_shock(pron_esc_modelo, esc)
    n = len(pron_base)
    dias = FRECUENCIAS[freq]["dias"]
    z = Z_NIVEL[par.nivel_servicio]
    P_per = max(par.revision_dias / dias, 1e-6)

    dec_base = decidir(entidad, pron_base, freq, par)
    par_esc = Parametros(lead_time_dias=par.lead_time_dias + esc.retraso_dias(), revision_dias=par.revision_dias,
                         nivel_servicio=par.nivel_servicio, inventario_actual=par.inventario_actual,
                         errores=par.errores)
    desde_evento = pron_esc.iloc[esc.desde:].reset_index(drop=True)
    dec_esc = decidir(entidad, desde_evento if len(desde_evento) else pron_esc, freq, par_esc)

    lt_base = lead_time_por_periodo(par.lead_time_dias, Escenario([], 0, 0), n, freq)
    lt_esc = lead_time_por_periodo(par.lead_time_dias, esc, n, freq)
    k = int(np.ceil(lt_esc.max())) + 1
    lt_plan = _anticipar(lt_esc, k)

    rop_b, meta_b, ss_b, _ = politica_dinamica(pron_base, lt_base, P_per, z, par.errores)
    rop_e, meta_e, ss_e, _ = politica_dinamica(pron_esc, lt_plan, P_per, z, par.errores)
    difiere = np.where(np.abs(rop_e - rop_b) > 0.01 * np.maximum(rop_b, 1e-9))[0]
    inicio_ajuste = int(difiere[0]) if len(difiere) else esc.desde

    t_ev = min(esc.desde, n - 1)
    resumen = {
        "rop_base": float(rop_b[t_ev]), "rop_esc": float(rop_e[t_ev]),
        "ss_base": float(ss_b[t_ev]), "ss_esc": float(ss_e[t_ev]),
        "meta_base": float(meta_b[t_ev]), "meta_esc": float(meta_e[t_ev]),
    }
    sims = (None, None, None)
    if par.inventario_actual is not None and not np.isnan(par.inventario_actual):
        I0 = par.inventario_actual
        f = pron_base["fecha"].to_numpy()
        s_base = simular(f, pron_base["P50"].to_numpy(), I0, rop_b, meta_b, lt_base)
        s_sin = simular(f, pron_esc["P50"].to_numpy(), I0, rop_b, meta_b, lt_esc, plan=pron_base["P50"].to_numpy())
        s_aj = simular(f, pron_esc["P50"].to_numpy(), I0, rop_e, meta_e, lt_esc)
        sims = (s_base, s_sin, s_aj)
        mundos = [("base", s_base), ("sin_ajuste", s_sin), ("ajustada", s_aj)]
        if esc.retraso_dias() and esc.costo_alternativo():
            # los pedidos del evento se compran al proveedor alternativo, que llega en el lead time normal
            s_alt = simular(f, pron_esc["P50"].to_numpy(), I0, rop_b, meta_b, lt_base, plan=pron_base["P50"].to_numpy())
            mundos.append(("alternativo", s_alt))
        for nombre, s in mundos:
            resumen[f"quiebre_{nombre}"] = int(s["quiebre"].sum())
            resumen[f"perdida_{nombre}"] = float(s["no_atendida"].sum())
            resumen[f"inv_prom_{nombre}"] = float(s["inventario"].mean())
            resumen[f"pedidos_{nombre}"] = int((s["pedido"] > 0).sum())
        # lo que agrega el evento por sobre lo que ya pasaría sin él
        # se compara el total (no día a día): un cambio de demanda puede correr unos días los quiebres
        # que ya existían sin que eso sea un efecto del evento
        resumen["quiebre_extra_sin"] = max(0, resumen["quiebre_sin_ajuste"] - resumen["quiebre_base"])
        resumen["quiebre_extra_aj"] = max(0, resumen["quiebre_ajustada"] - resumen["quiebre_base"])
        resumen["perdida_extra_sin"] = max(0.0, resumen["perdida_sin_ajuste"] - resumen["perdida_base"])
        resumen["perdida_extra_aj"] = max(0.0, resumen["perdida_ajustada"] - resumen["perdida_base"])
        # diferencias mínimas (menos de 0,5% de la demanda del horizonte) son ruido de la simulación discreta:
        # los pedidos se corren un día y un quiebre que ya existía cae en otra fecha
        if len(mundos) == 4:
            resumen["quiebre_extra_alt"] = max(0, resumen["quiebre_alternativo"] - resumen["quiebre_base"])
            resumen["perdida_extra_alt"] = max(0.0, resumen["perdida_alternativo"] - resumen["perdida_base"])
        tolerancia = 0.005 * float(pron_base["P50"].sum())
        for k in [k for k in ("sin", "aj", "alt") if f"perdida_extra_{k}" in resumen]:
            if resumen[f"perdida_extra_{k}"] < tolerancia:
                resumen[f"quiebre_extra_{k}"], resumen[f"perdida_extra_{k}"] = 0, 0.0
    resumen["demanda_base"] = float(pron_base["P50"].sum())
    resumen["demanda_esc"] = float(pron_esc["P50"].sum())
    ev = slice(esc.desde, esc.hasta)
    resumen["demanda_base_evento"] = float(pron_base["P50"].iloc[ev].sum())
    resumen["demanda_esc_evento"] = float(pron_esc["P50"].iloc[ev].sum())
    resumen["dias_por_periodo"] = dias
    # ¿el evento cambia algo del inventario? (si no, las tres curvas son la misma)
    if sims[0] is not None:
        ref = np.maximum(np.abs(sims[0]["inventario"].to_numpy()).max(), 1.0)
        resumen["inventario_igual"] = bool(
            np.abs(sims[1]["inventario"].to_numpy() - sims[0]["inventario"].to_numpy()).max() < 0.002 * ref and
            np.abs(sims[2]["inventario"].to_numpy() - sims[0]["inventario"].to_numpy()).max() < 0.002 * ref)
        ped = sims[0][sims[0]["pedido"] > 0]
        resumen["pedidos_en_evento"] = int(((ped.index >= esc.desde) & (ped.index < esc.hasta)).sum())
        resumen["proximo_pedido"] = ped["fecha"].iloc[0] if len(ped) else None
    comp = Comparacion(entidad, pron_base, pron_esc, dec_base, dec_esc, *sims, inicio_ajuste=inicio_ajuste,
                       resumen=resumen)
    if sims[0] is not None and len(mundos) == 4:
        comp.sim_alternativo = mundos[3][1]
    return comp


# ---------------------------------------------------------------- dinero

def precio_por_periodo(precio: float, esc: Escenario, n: int) -> np.ndarray:
    p = np.full(n, float(precio))
    p[esc.desde:esc.hasta] *= 1 + esc.cambio_precio()
    return p


def impacto_dinero(comp: Comparacion, esc: Escenario, precio: float | None, costo: float | None,
                   costo_principal: float | None = None, inflacion=None) -> pd.DataFrame | None:
    """Ingresos, costo y margen de cada mundo sobre el horizonte analizado.

    Las unidades vendidas son la demanda atendida (la simulación con venta perdida) o, si no hay
    inventario, la demanda pronosticada. El costo es el de lo vendido; si se cubre el retraso con el
    proveedor alternativo, los pedidos emitidos durante el evento pagan la diferencia de costo.
    """
    if precio is None or not np.isfinite(precio) or precio <= 0:
        return None
    n = len(comp.base)
    f = np.ones(n) if inflacion is None else np.asarray(inflacion, float)[:n]   # precios y costos con inflación
    p_base = np.full(n, float(precio)) * f
    p_esc = precio_por_periodo(precio, esc, n) * f
    c = float(costo) if costo is not None and np.isfinite(costo) and costo > 0 else None

    def vendidas(sim, pron):
        if sim is not None:
            return (sim["demanda"] - sim["no_atendida"]).to_numpy(), sim["no_atendida"].to_numpy()
        return pron["P50"].to_numpy(), np.zeros(n)

    mundos = [("Sin el evento", comp.sim_base, comp.base, p_base),
              ("Con el evento, sin ajustar", comp.sim_sin_ajuste, comp.escenario, p_esc)]
    if comp.sim_base is not None:
        mundos.append(("Con el evento, ajustando", comp.sim_ajustada, comp.escenario, p_esc))
    if comp.sim_alternativo is not None:
        mundos.append(("Con el evento, proveedor alternativo", comp.sim_alternativo, comp.escenario, p_esc))

    filas = []
    for nombre, sim, pron, p in mundos:
        v, perdida = vendidas(sim, pron)
        fila = {"mundo": nombre, "precio_evento": float(p[esc.desde:esc.hasta].mean()) if esc.duracion else float(p[0]),
                "unidades": float(v.sum()), "ingresos": float((v * p).sum()),
                "ventas_perdidas": float((perdida * p).sum()), "unidades_perdidas": float(perdida.sum())}
        sobrecosto = 0.0
        c_pri = costo_principal if costo_principal else c
        if nombre.endswith("alternativo") and c_pri and sim is not None:
            c_alt = esc.costo_alternativo()
            ped = sim["pedido"].to_numpy()
            sobrecosto = float((ped[esc.desde:esc.hasta] * f[esc.desde:esc.hasta]).sum() * max(0.0, c_alt - c_pri))
        fila["sobrecosto"] = sobrecosto
        if c is not None:
            fila["costo"] = float((v * f).sum() * c) + sobrecosto
            fila["margen"] = fila["ingresos"] - fila["costo"]
        filas.append(fila)
    return pd.DataFrame(filas)

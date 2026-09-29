import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import estilo as E
import sesion as S
from motor import politica as P

dp, res = S.requiere_pronostico()
S.panel_dataset()
fut = S.pronostico()
entidades = list(fut)
fi = dp.freq_info
H = S.horizonte()
nom = S.mayus(S.nombre_entidad(dp))

E.encabezado(
    "Paso 3",
    "Decisiones de abastecimiento",
    "El pronóstico convertido en qué hacer: cuánto stock de seguridad mantener, en qué nivel pedir, "
    "cuánto pedir y cuándo.",
)

# ---------------------------------------------------------------- parámetros del negocio
tiene_lt, tiene_inv = dp.tiene("lead_time"), dp.tiene("inventario")
base = []
for e in entidades:
    g = dp.df[dp.df["entidad"] == e]
    base.append({
        nom: e,
        "Lead time (días)": round(P.lead_time_dataset(g["lead_time"]), 1) if tiene_lt else 14.0,
        "Inventario actual": float(g["inventario"].iloc[-1]) if tiene_inv else np.nan,
    })
base = pd.DataFrame(base)
# si se abrió desde Mis pronósticos, se parte de la política que se había guardado
guardada = st.session_state.get("politica_guardada")
if guardada and guardada.get("clave") == S.clave_dataset(dp):
    previa = {f["entidad"]: f for f in guardada.get("tabla", [])}
    for i, e in enumerate(base[nom]):
        if e in previa:
            if previa[e].get("lead_time") is not None:
                base.loc[i, "Lead time (días)"] = previa[e]["lead_time"]
            if previa[e].get("inventario") is not None:
                base.loc[i, "Inventario actual"] = previa[e]["inventario"]
else:
    guardada = None

falta = []
if not tiene_lt:
    falta.append("el lead time (se usan 14 días por defecto)")
if not tiene_inv:
    falta.append("el inventario actual")

with st.container(border=True):
    c1, c2 = st.columns(2)
    nivel = c1.select_slider("Nivel de servicio", list(P.Z_NIVEL), value=(guardada or {}).get("nivel", "90%"),
                             help="Probabilidad de no quedarse sin stock mientras llega un pedido. Más alto = más stock de seguridad.")
    rev_def = {"D": 30, "W": 28, "M": 30, "Q": 91}[dp.config.frecuencia]
    revision = c2.number_input("Cada cuántos días revisas y pides", 1, 365, int((guardada or {}).get("revision", rev_def)),
                               help="Período de revisión (P) de la política.")
    with st.expander("Lead time e inventario por " + S.nombre_entidad(dp), expanded=bool(falta), icon=":material/edit:"):
        if falta:
            st.caption(":material/info: Tu archivo no trae " + " ni ".join(falta) + ". Complétalos aquí para obtener las decisiones.")
        else:
            st.caption("Tomados de tu archivo. Puedes ajustarlos.")
        editado = st.data_editor(
            base, hide_index=True, width="stretch", key=f"editor_{S.clave_dataset(dp)}",
            disabled=[nom],
            column_config={
                "Lead time (días)": st.column_config.NumberColumn(min_value=0.0, step=1.0, format="%.1f"),
                "Inventario actual": st.column_config.NumberColumn(min_value=0.0, step=1.0, format="%.0f"),
            },
        )

# se guarda para que Escenarios use la misma política
st.session_state["politica"] = {
    "clave": S.clave_dataset(dp), "nivel": nivel, "revision": revision,
    "tabla": editado.rename(columns={nom: "entidad", "Lead time (días)": "lead_time", "Inventario actual": "inventario"}),
}
S.actualizar_registro(politica=S.politica_a_json(st.session_state["politica"]["tabla"], nivel, int(revision)))

# ---------------------------------------------------------------- decisiones
# el pedido cubre lead time + revisión: si el horizonte elegido es más corto, se extiende internamente
necesarios = int(np.ceil((editado["Lead time (días)"].max() + revision) / fi["dias"])) + 1
# se usa el horizonte máximo para que la fecha del próximo pedido no dependa del largo elegido
# (la política se recalcula con la demanda que viene, y cortar el pronóstico antes la distorsiona)
H_dec = res.plan.horizonte_max
if H_dec != H:
    fut = S.pronostico(H_dec)
decs = {}
for _, fila in editado.iterrows():
    e = fila[nom]
    inv = fila["Inventario actual"]
    par = P.Parametros(lead_time_dias=float(fila["Lead time (días)"]), revision_dias=float(revision),
                       nivel_servicio=nivel, inventario_actual=None if pd.isna(inv) else float(inv))
    decs[e] = P.decidir(e, fut[e], dp.config.frecuencia, par)

n_pedir = sum(d.estado == "Pedir ahora" for d in decs.values())
n_riesgo = sum(d.estado == "Riesgo de quiebre" for d in decs.values())
unidades = sum(d.cantidad for d in decs.values() if d.estado in ("Pedir ahora", "Riesgo de quiebre"))
proximas = [d.fecha_pedido for d in decs.values() if d.estado == "Stock suficiente" and d.fecha_pedido is not None]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Pedir ahora", n_pedir)
c2.metric("En riesgo de quiebre", n_riesgo, help="El inventario ya está bajo el stock de seguridad.")
c3.metric("Unidades a pedir hoy", E.num(unidades))
c4.metric("Próximo pedido programado", min(proximas).strftime("%d/%m/%Y") if proximas else "—")

ESTADOS = {"Riesgo de quiebre": "🔴 Riesgo de quiebre", "Pedir ahora": "🟠 Pedir ahora",
           "Stock suficiente": "🟢 Stock suficiente", "Sin inventario": "⚪ Falta inventario"}
orden = {"Riesgo de quiebre": 0, "Pedir ahora": 1, "Stock suficiente": 2, "Sin inventario": 3}
filas = []
for e, d in sorted(decs.items(), key=lambda x: (orden[x[1].estado], str(x[0]))):
    filas.append({
        nom: e,
        "Estado": ESTADOS[d.estado],
        "Inventario": d.inventario,
        "Stock de seguridad": d.ss,
        "Punto de reorden hoy": d.rop,
        "Meta (T) hoy": d.meta,
        "Próximo pedido (u.)": d.cantidad if d.estado != "Sin inventario" else np.nan,
        "Fecha del próximo pedido": d.fecha_pedido.strftime("%d/%m/%Y") if d.fecha_pedido is not None else
        ("—" if d.estado != "Sin inventario" else ""),
        "Cobertura (días)": d.cobertura_dias,
    })
tabla = pd.DataFrame(filas)
st.dataframe(tabla, width="stretch", hide_index=True, column_config={
    c: st.column_config.NumberColumn(format="%.0f") for c in
    ["Inventario", "Stock de seguridad", "Punto de reorden hoy", "Meta (T) hoy", "Próximo pedido (u.)", "Cobertura (días)"]
})

# ---------------------------------------------------------------- detalle
st.write("")
st.markdown(f"## Detalle por {S.nombre_entidad(dp)}")
ent = S.selector_entidad(entidades, dp, key="dec")
d = decs[ent]
pr = fut[ent]                       # horizonte máximo: alcanza para ver llegar los pedidos
u = fi["unidad"]
dias_p = fi["dias"]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stock de seguridad hoy", E.num(d.ss))
m2.metric("Punto de reorden hoy", E.num(d.rop),
          help="Se recalcula cada día con la demanda que viene; en el gráfico se ve cómo cambia.")
m3.metric("Meta (T) hoy", E.num(d.meta),
          help="Nivel al que cada pedido lleva la posición de inventario (bodega + pedidos en camino).")
m4.metric("Inventario actual", E.num(d.inventario) if d.inventario is not None else "—")

if d.inventario is None:
    E.nota(f"Ingresa el inventario actual de <b>{ent}</b> en <b>Lead time e inventario</b> (arriba) para ver la "
           "proyección del inventario y la fecha del próximo pedido.")
    st.stop()


def ultimo(rol):
    if not dp.tiene(rol):
        return None
    v = dp.df.loc[dp.df["entidad"] == ent, rol].dropna()
    return float(v.iloc[-1]) if len(v) and v.iloc[-1] > 0 else None


def figura(sim, titulo, y_max, marcar_manual=False):
    """Inventario en bodega, posición (bodega + en camino), meta, punto de reorden, pedidos y llegadas."""
    fig = go.Figure()
    ped = sim[sim["pedido"] > 0]
    lle = sim[sim["llegada"] > 0]
    for _, fp in ped.iterrows():   # tramo en camino de cada pedido
        llegadas = lle[lle["fecha"] > fp["fecha"]]
        if len(llegadas):
            fig.add_vrect(x0=fp["fecha"], x1=llegadas["fecha"].iloc[0], fillcolor=E.NARANJO, opacity=0.07, line_width=0)
    fig.add_trace(go.Scatter(x=sim["fecha"], y=sim["inventario"], name="Inventario en bodega", mode="lines",
                             line=dict(color=E.AZUL, width=2.4), hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=sim["fecha"], y=sim["posicion"], name="Posición (bodega + en camino)", mode="lines",
                             line=dict(color=E.AZUL, width=1.3, dash="dot"), opacity=0.6, hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=sim["fecha"], y=sim["meta"], name="Meta (T)", mode="lines",
                             line=dict(color=E.AMARILLO, width=1.3, dash="dashdot"), hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=sim["fecha"], y=sim["rop"], name="Punto de reorden", mode="lines",
                             line=dict(color=E.NARANJO, dash="dash", width=1.5), hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=sim["fecha"], y=sim["ss"], name="Stock de seguridad", mode="lines",
                             line=dict(color=E.ROJO, dash="dot", width=1.3), hovertemplate="%{y:,.0f}"))
    auto = ped[ped["pedido_manual"] <= 0]
    fig.add_trace(go.Scatter(x=auto["fecha"], y=auto["inventario"], mode="markers", name="Pedido del modelo",
                             marker=dict(color=E.NARANJO, size=10, symbol="triangle-up", line=dict(color="white", width=1.5)),
                             customdata=auto["pedido"], hovertemplate="pedido de %{customdata:,.0f} u."))
    if True:   # siempre en la leyenda, para que los dos gráficos tengan el mismo tamaño
        man = ped[ped["pedido_manual"] > 0]
        fig.add_trace(go.Scatter(x=list(man["fecha"]) or [None], y=list(man["inventario"]) or [None], mode="markers", name="Tu compra",
                                 marker=dict(color="#4a3aa7", size=13, symbol="star", line=dict(color="white", width=1)),
                                 customdata=man["pedido_manual"], hovertemplate="tu compra: %{customdata:,.0f} u."))
    fig.add_trace(go.Scatter(x=lle["fecha"], y=lle["inventario"], mode="markers", name="Llega el pedido",
                             marker=dict(color=E.VERDE, size=10, symbol="diamond", line=dict(color="white", width=1.5)),
                             customdata=lle["llegada"], hovertemplate="llegan %{customdata:,.0f} u."))
    qb = sim[sim["quiebre"]]
    if True:
        fig.add_trace(go.Scatter(x=list(qb["fecha"]) or [None], y=list(qb["inventario"]) or [None], mode="markers",
                                 name="Sin stock",
                                 marker=dict(color=E.ROJO, size=8, symbol="x"),
                                 customdata=qb["no_atendida"], hovertemplate="faltan %{customdata:,.0f} u."))
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name="Pedido en camino",
                             marker=dict(symbol="square", size=12, color=E.NARANJO, opacity=0.25)))
    fig.update_layout(title=titulo, yaxis_title="Unidades", height=560, margin=dict(t=56, b=8),
                      yaxis=dict(range=[0, y_max * 1.05]),
                      legend=dict(orientation="h", yanchor="top", y=-0.17, xanchor="left", x=0))
    return fig


def lectura_pedido(sim):
    ped = sim[sim["pedido"] > 0]
    lle = sim[sim["llegada"] > 0]
    if not len(ped):
        return "No se emite ningún pedido en el horizonte analizado."
    f_ped = ped["fecha"].iloc[0]
    llegadas = lle[lle["fecha"] > f_ped]
    t = (f"El pedido del {f_ped:%d/%m/%Y} ({E.num(ped['pedido'].iloc[0])} u.) lleva la posición hasta la meta")
    if len(llegadas):
        t += (f" y llega el {llegadas['fecha'].iloc[0]:%d/%m/%Y} (lead time de {E.num(d.L * dias_p)} días). "
              "Mientras va en camino (zona sombreada) la bodega sigue bajando: por eso la bodega nunca llega a la meta, "
              "solo la posición.")
    return t


sim_sug = P.simular(pr, d)

# ---------------------------------------------------------------- ¿quién decide?
modo = S.elegir_uno("¿Quién decide las compras?", ["Automático", "Tú decides"], key="modo", estado="modo_compra")

if modo == "Automático":
    col_t, col_m = st.columns([1.5, 1], gap="large")
    with col_m:
        if d.estado == "Riesgo de quiebre":
            mensaje = (f"<b>{ent}</b> tiene {E.num(d.inventario)} unidades, bajo el stock de seguridad. "
                       f"Pide <b>{E.num(d.cantidad)} unidades</b> hoy; es probable un quiebre antes de que llegue.")
        elif d.estado == "Pedir ahora":
            mensaje = (f"<b>{ent}</b> tiene {E.num(d.inventario)} unidades, bajo el punto de reorden ({E.num(d.rop)}). "
                       f"Pide <b>{E.num(d.cantidad)} unidades</b> hoy para llegar a la meta.")
        elif d.fecha_pedido is not None:
            mensaje = (f"<b>{ent}</b> tiene stock para unos {E.num(d.cobertura_dias)} días. Según el pronóstico, "
                       f"el próximo pedido será el <b>{d.fecha_pedido:%d/%m/%Y}</b>, por <b>{E.num(d.cantidad)} unidades</b>.")
        else:
            mensaje = (f"<b>{ent}</b> tiene stock para unos {E.num(d.cobertura_dias)} días: no necesita pedido dentro "
                       f"de los {len(pr)} {fi['unidad_pl']} analizados.")
        E.nota(mensaje)
        st.caption(f"Demanda pronosticada {E.num(d.d, 1)} por {u} · incertidumbre σ = {E.num(d.sigma, 1)} · "
                   f"lead time {E.num(d.L * dias_p, 1)} días · revisión {E.num(d.P * dias_p, 1)} días · "
                   f"nivel de servicio {nivel}.")
        if d.aviso:
            st.caption(":material/warning: " + d.aviso)
        with st.expander("Cómo se calcula", icon=":material/function:"):
            st.latex(r"SS = Z \cdot \sigma \cdot \sqrt{L} \qquad ROP = \sum_{L} d + SS \qquad T = \sum_{L+P} d + SS")
            st.caption("Cada día se suma la demanda pronosticada de los próximos L días (punto de reorden) y de los "
                       "próximos L + P días (meta); σ sale del ancho del rango P10–P90. Cuando la posición de inventario "
                       "(bodega + pedidos en camino) llega al punto de reorden, se pide lo necesario para subirla a la meta: "
                       "cantidad = meta de ese día − posición.")
    with col_t.container(border=True):
        E.grafico(figura(sim_sug, f"Sugerencia del modelo · {ent}", float(sim_sug[["posicion", "meta"]].max().max())),
                  key="fig_inv")
        st.caption(lectura_pedido(sim_sug))
    sim_tuyo, plan_activo, ev = None, None, None
else:
    # ------------------------------------------------------------ tú decides
    precio = ultimo("precio")
    costo_arch = ultimo("costo_unitario")
    c1, c2, c3 = st.columns([1, 1, 1])
    costo = c1.number_input("Costo por unidad que pagas ($)", 0.0, None, float(round(costo_arch or 0)), 10.0,
                            format="%.0f", key=f"costo_plan_{ent}",
                            help="Lo que te cuesta comprar una unidad. Con esto se calcula el gasto y el ahorro. "
                                 "Déjalo en 0 si no quieres ver montos.") or None
    if precio:
        c2.metric("Precio de venta (de tu archivo)", E.clp(precio))
    if costo and precio and costo >= precio:
        c3.caption(":material/warning: El costo es igual o mayor que el precio de venta: revisa el costo. Con este valor "
                   "no se calcula el margen.")
    sug_ped = sim_sug[sim_sug["pedido"] > 0]
    f0 = sug_ped["fecha"].iloc[0] if len(sug_ped) else pr["fecha"].iloc[0]
    q0 = float(sug_ped["pedido"].iloc[0]) if len(sug_ped) else 0.0

    st.markdown("**Tu decisión de compra**")
    st.caption(f"El modelo sugiere pedir **{E.num(q0)} u.** el **{f0:%d/%m/%Y}**. Cambia la cantidad (o la fecha) y "
               "presiona **Activar compra**. Después de tu compra, el modelo vuelve a planificar en la siguiente "
               f"revisión ({E.num(revision)} días después), con el stock que realmente tengas.")
    por = "Unidades"
    if costo:
        por = S.elegir_uno("Defino la compra por", ["Unidades", "Presupuesto ($)"], key="por", estado="plan_por")
    clave_plan = f"{S.clave_dataset(dp)}_{ent}"
    base_plan = pd.DataFrame({"Fecha de compra": [f0.date()], "Cantidad (u.)": [float(round(q0))],
                              "Lead time (días)": [float(round(d.L * dias_p, 1))]})
    if por == "Presupuesto ($)":
        b1, _ = st.columns([1, 2])
        presupuesto = b1.number_input("Presupuesto para esta compra ($)", 0.0, None, float(round(q0 * costo)), 1000.0,
                                      format="%.0f", key=f"pres_{clave_plan}")
        base_plan.loc[0, "Cantidad (u.)"] = float(np.floor(presupuesto / costo))
        st.caption(f"Con {E.clp_md(presupuesto)} alcanzas a comprar **{E.num(base_plan.loc[0, 'Cantidad (u.)'])} u.** "
                   f"a {E.clp_md(costo)} cada una.")
    plan = st.data_editor(
        base_plan, num_rows="dynamic", hide_index=True, width="stretch",
        key=f"plan_{clave_plan}_{por}",
        column_config={
            "Fecha de compra": st.column_config.DateColumn(format="DD/MM/YYYY", min_value=pr["fecha"].iloc[0].date(),
                                                           max_value=pr["fecha"].iloc[-1].date(), required=True),
            "Cantidad (u.)": st.column_config.NumberColumn(min_value=0.0, step=1.0, format="%.0f", required=True),
            "Lead time (días)": st.column_config.NumberColumn(min_value=0.0, step=1.0, format="%.1f",
                                                              help="Puedes cambiarlo, por ejemplo, para una compra urgente."),
        },
    )
    planes = st.session_state.setdefault("planes_compra", {})
    b1, b2 = st.columns([1, 3], vertical_alignment="center")
    if b1.button("Activar compra", type="primary", icon=":material/shopping_cart_checkout:"):
        planes[clave_plan] = plan.dropna(subset=["Fecha de compra", "Cantidad (u.)"]).copy()
    plan_activo = planes.get(clave_plan)
    if plan_activo is None:
        b2.caption("Todavía no activas una compra: abajo solo ves la sugerencia del modelo.")
    elif not plan.dropna(subset=["Fecha de compra", "Cantidad (u.)"]).reset_index(drop=True).equals(
            plan_activo.reset_index(drop=True)):
        b2.caption(":material/edit: Tienes cambios sin activar.")

    sim_tuyo, ev = None, None
    if plan_activo is not None and len(plan_activo):
        fechas = pr["fecha"].dt.normalize()
        manuales = {}
        for _, fila in plan_activo.iterrows():
            f = pd.Timestamp(fila["Fecha de compra"])
            idx = int(np.searchsorted(fechas.to_numpy(), np.datetime64(f)))
            if idx < len(pr):
                q_prev = manuales.get(idx, (0.0, 0.0))[0]
                manuales[idx] = (q_prev + float(fila["Cantidad (u.)"]), float(fila["Lead time (días)"]) / dias_p)
        if manuales:
            auto_desde = max(manuales) + int(np.ceil(d.P))
            sim_tuyo = P.simular(pr, d, manuales=manuales, auto_desde=auto_desde)
            ev = P.evaluar_plan(sim_sug, sim_tuyo, auto_desde, precio=precio,
                                costo=costo if (costo and (not precio or costo < precio)) else costo,
                                dias_periodo=dias_p)
            ev["auto_desde"] = pr["fecha"].iloc[min(auto_desde, len(pr) - 1)]
            ev["manuales"] = manuales

    y_max = float(max(sim_sug[["posicion", "meta"]].max().max(),
                      sim_tuyo[["posicion", "meta"]].max().max() if sim_tuyo is not None else 0))
    g1, g2 = st.columns(2, gap="medium")
    with g1.container(border=True):
        E.grafico(figura(sim_sug, f"Sugerencia del modelo · {ent}", y_max), key="fig_sug")
        st.caption(lectura_pedido(sim_sug))
    with g2.container(border=True):
        if sim_tuyo is None:
            st.markdown(f"**Tu plan · {ent}**")
            st.caption("Ingresa tu compra arriba y presiona **Activar compra** para ver cómo quedaría tu inventario.")
        else:
            E.grafico(figura(sim_tuyo, f"Tu plan · {ent}", y_max, marcar_manual=True), key="fig_tuyo")
            st.caption(f"Tus compras (estrella morada) hasta el {ev['auto_desde']:%d/%m/%Y}; desde ahí el modelo vuelve "
                       "a comprar solo (triángulos).")

    # ------------------------------------------------------------ respuestas
    if ev is not None:
        st.markdown(f"### Qué significa tu decisión · {ent}")
        k1, k2, k3, k4 = st.columns(4)
        dif_u = ev["compra_tuya"] - ev["compra_sug"]
        k1.metric("Compras en tu período", f"{E.num(ev['compra_tuya'])} u.",
                  delta=f"{E.num(dif_u).replace('−', '-')} u. vs. sugerido" if abs(dif_u) >= 1 else "igual que el sugerido",
                  delta_color="off", delta_arrow="auto" if abs(dif_u) >= 1 else "off",
                  help=f"Compras entre hoy y el {ev['auto_desde']:%d/%m/%Y}, cuando el modelo vuelve a decidir.")
        if "ahorro_caja" in ev:
            k2.metric("Caja que liberas", E.clp(ev["ahorro_caja"]),
                      delta=f"gastas {E.clp_md(ev['gasto_tuyo'])} en vez de {E.clp_md(ev['gasto_sug'])}",
                      delta_color="off", delta_arrow="off",
                      help="Es un ahorro de caja en este período: lo que no compras ahora se compra más adelante.")
        else:
            k2.metric("Caja que liberas", "—", help="Ingresa el costo por unidad para ver montos.")
        dias_q = (ev["quiebres_tuyo"] - ev["quiebres_sug"]) * dias_p
        k3.metric("Días sin stock que agregas", E.num(max(0, dias_q)),
                  delta=f"{E.num(ev['perdida_extra'])} u. sin vender" if ev["perdida_extra"] > 0 else "sin quiebres extra",
                  delta_color="inverse" if ev["perdida_extra"] > 0 else "off",
                  delta_arrow="off")
        if "ventas_perdidas" in ev:
            k4.metric("Ventas que pierdes", E.clp(ev["ventas_perdidas"]),
                      delta=(f"margen perdido {E.clp_md(ev['margen_perdido'])}" if "margen_perdido" in ev else None),
                      delta_color="off", delta_arrow="off",
                      help="Unidades sin stock × precio de venta. A diferencia del ahorro, esta venta no se recupera.")
        else:
            k4.metric("Ventas que pierdes", f"{E.num(ev['perdida_extra'])} u.",
                      help="Tu archivo no trae precio de venta: se muestra en unidades.")

        # la respuesta completa, en palabras
        man = ev["manuales"]
        compras_txt = "; ".join(
            f"<b>{E.num(q)} u.</b> el {pr['fecha'].iloc[t]:%d/%m/%Y}, que llegan el "
            f"{pr['fecha'].iloc[min(len(pr) - 1, t + max(1, int(np.ceil(lt))))]:%d/%m/%Y}"
            for t, (q, lt) in sorted(man.items()))
        frases = [f"Con tu plan compras {compras_txt} (el modelo sugería {E.num(ev['compra_sug'])} u. en ese período)."]
        if "ahorro_caja" in ev and ev["ahorro_caja"] > 0:
            frases.append(f"Liberas <b>{E.clp(ev['ahorro_caja'])}</b> de caja en este período.")
        elif "ahorro_caja" in ev and ev["ahorro_caja"] < 0:
            frases.append(f"Gastas <b>{E.clp(-ev['ahorro_caja'])}</b> más que lo sugerido en este período.")
        if ev["perdida_extra"] > 0:
            frases.append(f"A cambio, te quedas sin stock desde el <b>{ev['primer_quiebre']:%d/%m/%Y}</b> y dejas de vender "
                          f"<b>{E.num(ev['perdida_extra'])} u.</b>"
                          + (f" (<b>{E.clp(ev['ventas_perdidas'])}</b> en ventas" +
                             (f", {E.clp(ev['margen_perdido'])} de margen)" if "margen_perdido" in ev else ")")
                             if "ventas_perdidas" in ev else "") + ".")
            if ev.get("dias_absorber"):
                base_abs = "margen" if "margen_perdido" in ev else "ventas"
                frases.append(f"Recuperar esa pérdida equivale a unos <b>{E.num(ev['dias_absorber'])} días</b> de {base_abs} "
                              "normales.")
            if "ahorro_caja" in ev and "margen_perdido" in ev:
                if ev["margen_perdido"] > ev["ahorro_caja"]:
                    frases.append("El margen que pierdes es mayor que la caja que liberas: el recorte sale caro.")
                else:
                    frases.append("La caja que liberas es mayor que el margen que pierdes, pero recuerda que la caja solo "
                                  "se posterga y la venta perdida no vuelve.")
        elif ev["compra_tuya"] < ev["compra_sug"]:
            frases.append("Aun así no te quedas sin stock: el inventario alcanza hasta la siguiente compra.")
        if ev["normaliza"] is not None:
            frases.append(f"El modelo retoma las compras el {ev['auto_desde']:%d/%m/%Y} y la operación se normaliza el "
                          f"<b>{ev['normaliza']:%d/%m/%Y}</b>, unos <b>{E.num(ev['dias_normaliza'])} días</b> desde hoy.")
        else:
            frases.append(f"Dentro de los {E.num(len(pr) * dias_p)} días analizados la operación no vuelve a la "
                          "trayectoria sugerida.")
        E.nota(" ".join(frases))

        resumen = pd.DataFrame({
            "": ["Compras en tu período (u.)", "Compras en todo el horizonte (u.)", "Días sin stock",
                 "Unidades sin vender", "Inventario promedio en bodega (u.)"],
            "Sugerencia del modelo": [ev["compra_sug"], ev["compra_total_sug"], ev["quiebres_sug"] * dias_p,
                                      ev["perdida_sug"], ev["inv_prom_sug"]],
            "Tu plan": [ev["compra_tuya"], ev["compra_total_tuya"], ev["quiebres_tuyo"] * dias_p,
                        ev["perdida_tuya"], ev["inv_prom_tuyo"]],
        })
        for c in ("Sugerencia del modelo", "Tu plan"):
            resumen[c] = resumen[c].map(E.num)
        if "gasto_sug" in ev:
            resumen.loc[len(resumen)] = ["Gasto en tu período", E.clp(ev["gasto_sug"]), E.clp(ev["gasto_tuyo"])]
        if "ventas_perdidas" in ev:
            resumen.loc[len(resumen)] = ["Ventas perdidas por quiebres", E.clp(ev["perdida_sug"] * precio),
                                         E.clp(ev["perdida_tuya"] * precio)]
        st.dataframe(resumen, hide_index=True, width="stretch")

# ---------------------------------------------------------------- descarga
exp = tabla.copy()
exp["Estado"] = exp["Estado"].str[2:]
hojas = {"Decisiones": exp.round(1)}


def hoja_sim(sim):
    h = sim[["fecha", "demanda", "inventario", "posicion", "pedido", "llegada", "no_atendida", "rop", "meta", "ss"]].copy()
    h["fecha"] = h["fecha"].dt.date
    return h.rename(columns={"fecha": "Fecha", "demanda": "Demanda pronosticada", "inventario": "Inventario en bodega",
                             "posicion": "Posición (bodega + en camino)", "pedido": "Pedido emitido",
                             "llegada": "Llegada", "no_atendida": "Demanda sin atender", "rop": "Punto de reorden",
                             "meta": "Meta (T)", "ss": "Stock de seguridad"}).round(1)


hojas[f"Sugerencia {ent}"[:31]] = hoja_sim(sim_sug)
if sim_tuyo is not None:
    hojas[f"Tu plan {ent}"[:31]] = hoja_sim(sim_tuyo)
st.download_button("Descargar decisiones (Excel)", E.excel_bytes(hojas),
                   file_name="decisiones_abastecimiento.xlsx", icon=":material/download:",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

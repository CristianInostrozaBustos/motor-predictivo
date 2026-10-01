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
    revision = c2.number_input("Días que cubre cada pedido", 1, 365, int((guardada or {}).get("revision", rev_def)),
                               help="Cada pedido alcanza para este período. Como mínimo se usa el lead time + 20%, "
                                    "para que llegue un pedido antes de necesitar el siguiente.")
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
                       nivel_servicio=nivel, inventario_actual=None if pd.isna(inv) else float(inv),
                       errores=S.errores_modelo(e))
    decs[e] = P.decidir(e, fut[e], dp.config.frecuencia, par)

n_pedir = sum(d.estado == "Pedir ahora" for d in decs.values())
n_riesgo = sum(d.estado == "Riesgo de quiebre" for d in decs.values())
unidades = sum(d.cantidad for d in decs.values() if d.estado in ("Pedir ahora", "Riesgo de quiebre"))
proximas = [d.fecha_pedido for d in decs.values() if d.estado == "Stock suficiente" and d.fecha_pedido is not None]

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
# vista resumida; el detalle técnico va en el Excel
vista = tabla[[nom, "Estado", "Inventario", "Fecha del próximo pedido", "Próximo pedido (u.)", "Cobertura (días)"]].rename(
    columns={"Próximo pedido (u.)": "Cantidad sugerida (u.)", "Cobertura (días)": "Te alcanza para (días)"})
for c in ["Inventario", "Cantidad sugerida (u.)", "Te alcanza para (días)"]:
    vista[c] = vista[c].map(lambda v: "" if pd.isna(v) else E.num(v))
st.dataframe(vista, width="stretch", hide_index=True)

# ---------------------------------------------------------------- detalle
st.write("")
st.markdown(f"## Detalle por {S.nombre_entidad(dp)}")
ent = S.selector_entidad(entidades, dp, key="dec")
d = decs[ent]
pr = fut[ent]                       # horizonte máximo: alcanza para ver llegar los pedidos
u = fi["unidad"]
dias_p = fi["dias"]

if d.inventario is None:
    E.nota(f"Ingresa el inventario actual de <b>{ent}</b> en <b>Lead time e inventario</b> (arriba) para ver la "
           "proyección del inventario y la fecha del próximo pedido.")
    st.stop()

# ---------------------------------------------------------------- lo esencial, en 4 tarjetas
m1, m2, m3, m4 = st.columns(4)
m1.metric("Inventario actual", f"{E.num(d.inventario)} u.")
if d.fecha_pedido is None:
    m2.metric("Próximo pedido", "—", help=f"No hace falta pedir en los {E.num(len(pr) * dias_p)} días analizados.")
else:
    m2.metric("Próximo pedido", "Hoy" if d.estado in ("Pedir ahora", "Riesgo de quiebre") else f"{d.fecha_pedido:%d/%m/%Y}")
m3.metric("Cantidad sugerida", f"{E.num(d.cantidad)} u." if d.fecha_pedido is not None else "—")
m4.metric("Te alcanza para", f"{E.num(d.cobertura_dias)} días" if d.cobertura_dias is not None else "—",
          help="Días que dura el inventario actual con la demanda pronosticada.")
cc_ent = S.costo_compra(ent)
if cc_ent:
    cap = d.inventario * cc_ent
    st.caption(f":material/savings: Capital en inventario hoy: **{E.clp_md(cap)}** · mantenerlo cuesta ≈ "
               f"**{E.clp_md(cap * S.costo_mantener_pct() / 100 / 12)} al mes** "
               f"({E.num(S.costo_mantener_pct())}% anual; se ajusta en Finanzas).")
tecnico = st.toggle("Ver detalle técnico", key="dec_tecnico",
                    help="Stock de seguridad, punto de reorden, meta y cómo se calculan.")
if tecnico:
    with st.container(border=True):
        t1, t2, t3 = st.columns(3)
        t1.metric("Stock de seguridad hoy", E.num(d.ss))
        t2.metric("Punto de reorden hoy", E.num(d.rop), help="Se recalcula cada día con la demanda que viene.")
        t3.metric("Meta (T) hoy", E.num(d.meta),
                  help="Nivel al que cada pedido lleva la posición de inventario (bodega + pedidos en camino).")
        st.latex(r"SS = Z \cdot \sigma_L \qquad ROP = \sum_{L} d + SS \qquad T = \sum_{L+P} d + SS")
        st.caption(f"σ_L = {E.num(d.sigma_lt)} u.: cuánto se equivocó el modelo, en total, en períodos del largo del "
                   f"lead time cuando lo probamos con datos pasados (la regla simple σ·√L daría {E.num(d.sigma * np.sqrt(d.L))}). "
                   "Se usa el mayor de los dos, porque los errores de días seguidos se parecen y se acumulan.")
        st.caption(f"Demanda pronosticada {E.num(d.d, 1)} por {u} · incertidumbre diaria σ = {E.num(d.sigma, 1)} · "
                   f"lead time {E.num(d.L * dias_p, 1)} días · revisión {E.num(d.P * dias_p, 1)} días · "
                   f"nivel de servicio {nivel}. Cuando la posición (bodega + pedidos en camino) llega al punto de "
                   "reorden, se pide lo necesario para subirla a la meta. Por eso la bodega nunca llega a la meta: "
                   "mientras el pedido viaja, se sigue vendiendo.")
        if d.aviso:
            st.caption(":material/warning: " + d.aviso)


def ultimo(rol):
    if not dp.tiene(rol):
        return None
    v = dp.df.loc[dp.df["entidad"] == ent, rol].dropna()
    return float(v.iloc[-1]) if len(v) and v.iloc[-1] > 0 else None


def fig_automatico(sim):
    fig = go.Figure()
    ped, lle = sim[sim["pedido"] > 0], sim[sim["llegada"] > 0]
    for _, fp in ped.iterrows():
        llegadas = lle[lle["fecha"] > fp["fecha"]]
        if len(llegadas):
            fig.add_vrect(x0=fp["fecha"], x1=llegadas["fecha"].iloc[0], fillcolor=E.NARANJO, opacity=0.06, line_width=0)
    fig.add_trace(go.Scatter(x=sim["fecha"], y=sim["inventario"], name="Inventario en bodega", mode="lines",
                             line=dict(color=E.AZUL, width=2.4), hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=sim["fecha"], y=sim["ss"], name="Stock de seguridad (SS)", mode="lines",
                             line=dict(color=E.ROJO, dash="dot", width=1.4), hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=sim["fecha"], y=sim["rop"], name="Punto de reorden (ROP)", mode="lines",
                             line=dict(color=E.NARANJO, dash="dash", width=1.4), hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=sim["fecha"], y=sim["meta"], name="Meta (T)", mode="lines",
                             line=dict(color=E.AMARILLO, width=1.4, dash="dashdot"), hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=list(ped["fecha"]) or [None], y=list(ped["inventario"]) or [None], mode="markers",
                             name="Se pide", marker=dict(color=E.NARANJO, size=11, symbol="triangle-up",
                                                         line=dict(color="white", width=1.5)),
                             customdata=list(ped["pedido"]) or [None], hovertemplate="se piden %{customdata:,.0f} u."))
    fig.add_trace(go.Scatter(x=list(lle["fecha"]) or [None], y=list(lle["inventario"]) or [None], mode="markers",
                             name="Llega", marker=dict(color=E.VERDE, size=10, symbol="diamond",
                                                       line=dict(color="white", width=1.5)),
                             customdata=list(lle["llegada"]) or [None], hovertemplate="llegan %{customdata:,.0f} u."))
    fig.update_layout(title=f"Inventario proyectado · {ent}", yaxis_title="Unidades", height=430, margin=dict(t=90))
    return fig


def fig_comparar(sug, tuyo):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=sug["fecha"], y=sug["inventario"], name="Sugerencia del modelo", mode="lines",
                             line=dict(color=E.TINTA_MUTED, width=1.8, dash="dot"), hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=tuyo["fecha"], y=tuyo["inventario"], name="Tu plan", mode="lines",
                             line=dict(color=E.AZUL, width=2.6), hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=tuyo["fecha"], y=tuyo["ss"], name="Stock de seguridad (SS)", mode="lines",
                             line=dict(color=E.ROJO, dash="dot", width=1.2), hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=tuyo["fecha"], y=tuyo["rop"], name="Punto de reorden (ROP)", mode="lines",
                             line=dict(color=E.NARANJO, dash="dash", width=1.2), hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=tuyo["fecha"], y=tuyo["meta"], name="Meta (T)", mode="lines",
                             line=dict(color=E.AMARILLO, width=1.2, dash="dashdot"), hovertemplate="%{y:,.0f}"))
    man = tuyo[tuyo["pedido_manual"] > 0]
    fig.add_trace(go.Scatter(x=man["fecha"], y=man["inventario"], mode="markers", name="Tu compra",
                             marker=dict(color="#4a3aa7", size=14, symbol="star", line=dict(color="white", width=1)),
                             customdata=man["pedido_manual"], hovertemplate="tu compra: %{customdata:,.0f} u."))
    qb = tuyo[tuyo["quiebre"]]
    if len(qb):
        fig.add_trace(go.Scatter(x=qb["fecha"], y=qb["inventario"], mode="markers", name="Sin stock",
                                 marker=dict(color=E.ROJO, size=8, symbol="x"),
                                 customdata=qb["no_atendida"], hovertemplate="faltan %{customdata:,.0f} u."))
    fig.update_layout(title=f"Tu plan vs. la sugerencia del modelo · {ent}", yaxis_title="Unidades", height=430,
                      margin=dict(t=90))
    return fig


sim_sug = P.simular(pr, d)
sim_tuyo, ev = None, None

# ---------------------------------------------------------------- ¿quién decide?
modo = S.elegir_uno("¿Quién decide la compra?", ["Automático", "Tú decides"], key="modo", estado="modo_compra")

if modo == "Automático":
    if d.estado == "Riesgo de quiebre":
        E.nota(f"🔴 <b>Pide hoy {E.num(d.cantidad)} u. de {ent}.</b> El inventario ya está bajo el stock de seguridad.")
    elif d.estado == "Pedir ahora":
        E.nota(f"🟠 <b>Pide hoy {E.num(d.cantidad)} u. de {ent}.</b>")
    elif d.fecha_pedido is not None:
        lle = sim_sug[(sim_sug["llegada"] > 0) & (sim_sug["fecha"] > d.fecha_pedido)]
        E.nota(f"🟢 <b>No necesitas pedir todavía.</b> El modelo pedirá <b>{E.num(d.cantidad)} u.</b> el "
               f"<b>{d.fecha_pedido:%d/%m/%Y}</b>"
               + (f", que llegan el {lle['fecha'].iloc[0]:%d/%m/%Y}." if len(lle) else "."))
    else:
        E.nota(f"🟢 <b>No necesitas pedir.</b> El inventario alcanza para los {E.num(len(pr) * dias_p)} días analizados.")
    with st.container(border=True):
        E.grafico(fig_automatico(sim_sug), key="fig_inv")
        st.caption("Cuando la bodega toca el punto de reorden (ROP) se hace el pedido. Mientras llega, la bodega "
                   "baja hasta cerca del stock de seguridad (SS); al llegar, sube hasta la meta (T).")
else:
    # ------------------------------------------------------------ tú decides
    precio = S.precio(ent)
    costo_arch = S.costo_compra(ent)
    sug_ped = sim_sug[sim_sug["pedido"] > 0]
    f0 = sug_ped["fecha"].iloc[0] if len(sug_ped) else pr["fecha"].iloc[0]
    q0 = float(sug_ped["pedido"].iloc[0]) if len(sug_ped) else 0.0
    clave_plan = f"{S.clave_dataset(dp)}_{ent}"

    with st.popover("Costo, fecha y lead time", icon=":material/tune:"):
        costo = st.number_input("Costo por unidad que pagas ($)", 0.0, None, float(round(costo_arch or 0)), 10.0,
                                format="%.0f", key=f"costo_plan_{ent}",
                                help="Para calcular cuánto ahorras. Déjalo en 0 si no quieres ver montos.") or None
        fecha_c = st.date_input("Fecha de la compra", f0.date(), min_value=pr["fecha"].iloc[0].date(),
                                max_value=pr["fecha"].iloc[-1].date(), format="DD/MM/YYYY", key=f"fecha_{clave_plan}")
        lt_c = st.number_input("Lead time (días)", 0.0, 365.0, float(round(d.L * dias_p, 1)), 1.0, format="%.1f",
                               key=f"lt_{clave_plan}", help="Cámbialo, por ejemplo, para una compra urgente.")
        if costo and precio and costo >= precio:
            st.caption(":material/warning: Este costo es igual o mayor que el precio de venta: probablemente es el "
                       "precio de un insumo y no el costo del producto.")

    c1, c2 = st.columns([1, 1.4], vertical_alignment="bottom")
    with c1:
        por = "Unidades"
        if costo:
            por = S.elegir_uno("Defino la compra en", ["Unidades", "Pesos ($)"], key="por", estado="plan_por")
        if por == "Unidades":
            q = st.number_input("¿Cuánto vas a comprar? (u.)", 0.0, None, float(round(q0)), 100.0, format="%.0f",
                                key=f"q_{clave_plan}")
        else:
            monto = st.number_input("¿Cuánto vas a gastar? ($)", 0.0, None, float(round(q0 * costo)), 10000.0,
                                    format="%.0f", key=f"m_{clave_plan}")
            q = float(np.floor(monto / costo))
    c2.caption(f"El modelo sugiere **{E.num(q0)} u.** el **{f0:%d/%m/%Y}**"
               + (f" ({E.clp_md(q0 * costo)})" if costo else "")
               + (f". Con tu monto compras **{E.num(q)} u.**" if por != "Unidades" else "") + ".")

    t_c = int(np.searchsorted(pr["fecha"].dt.normalize().to_numpy(), np.datetime64(pd.Timestamp(fecha_c))))
    t_c = min(t_c, len(pr) - 1)
    auto_desde = t_c + int(np.ceil(d.P))
    sim_tuyo = P.simular(pr, d, manuales={t_c: (q, lt_c / dias_p)}, auto_desde=auto_desde)
    margen_ok = costo if (costo and (not precio or costo < precio)) else None
    ev = P.evaluar_plan(sim_sug, sim_tuyo, auto_desde, precio=precio, costo=costo, dias_periodo=dias_p)

    with st.container(border=True):
        E.grafico(fig_comparar(sim_sug, sim_tuyo), key="fig_tuyo")

    # ------------------------------------------------------------ tres respuestas y un veredicto
    dif_q = ev["compra_sug"] - ev["compra_tuya"]
    parecido = abs(dif_q) <= max(0.01 * ev["compra_sug"], 1.0) and ev["perdida_extra"] <= 0
    k1, k2, k3 = st.columns(3)
    if costo:
        ahorro = ev["ahorro_caja"]
        k1.metric("Ahorras en esta compra" if (ahorro >= 0 or parecido) else "Gastas de más",
                  E.clp(abs(ahorro)) if not parecido else "$0")
    else:
        k1.metric("Compras de menos" if (dif_q >= 0 or parecido) else "Compras de más",
                  f"{E.num(abs(dif_q))} u." if not parecido else "0 u.")
    dias_sin = max(0, ev["quiebres_tuyo"] - ev["quiebres_sug"]) * dias_p
    if precio:
        k2.metric("Pierdes en ventas", E.clp(ev["ventas_perdidas"]),
                  help=f"{E.num(ev['perdida_extra'])} u. que no podrás vender por falta de stock.")
    else:
        k2.metric("Unidades que no vendes", f"{E.num(ev['perdida_extra'])} u.")
    al_final = ev["ultimo_quiebre"] is not None and ev["ultimo_quiebre"] >= pr["fecha"].iloc[-1]
    if ev["perdida_extra"] > 0:
        k3.metric("Días sin stock", ("≥ " if al_final else "") + E.num(dias_sin),
                  help=(f"Desde el {ev['primer_quiebre']:%d/%m/%Y} hasta el {ev['ultimo_quiebre']:%d/%m/%Y}."))
    else:
        k3.metric("Días sin stock", "0")

    if parecido:
        E.nota("⚪ <b>Es prácticamente lo mismo que sugiere el modelo.</b>")
    elif ev["perdida_extra"] <= 0:
        E.nota(f"🟢 <b>Buena decisión:</b> compras {E.num(ev['compra_tuya'])} u. en vez de {E.num(ev['compra_sug'])}"
               + (f" y ahorras <b>{E.clp(ev['ahorro_caja'])}</b>" if costo and ev["ahorro_caja"] > 0 else "")
               + " sin quedarte sin stock.")
    else:
        perdida_txt = E.clp(ev["ventas_perdidas"]) + " en ventas" if precio else f"{E.num(ev['perdida_extra'])} u."
        ahorro_txt = f"ahorras {E.clp(ev['ahorro_caja'])}, pero " if costo and ev["ahorro_caja"] > 0 else ""
        if ev["ultimo_quiebre"] is not None and ev["ultimo_quiebre"] < pr["fecha"].iloc[-1]:
            vuelta = f" Te recuperas el <b>{ev['ultimo_quiebre'] + pd.Timedelta(days=dias_p):%d/%m/%Y}</b>."
        else:
            # el pedido que te recupera puede llegar después del horizonte: se estima con el lead time
            auto = sim_tuyo[(sim_tuyo["pedido"] > 0) & (sim_tuyo["pedido_manual"] <= 0) &
                            (sim_tuyo.index >= auto_desde)]
            if len(auto):
                f_rec = auto["fecha"].iloc[0] + pd.Timedelta(days=float(np.ceil(d.L)) * dias_p)
                vuelta = (f" Te recuperas cerca del <b>{f_rec:%d/%m/%Y}</b>, cuando llega el pedido que el modelo "
                          f"hace el {auto['fecha'].iloc[0]:%d/%m/%Y}.")
            else:
                vuelta = " No alcanzas a recuperarte dentro del período analizado."
        E.nota(f"🔴 <b>Ojo:</b> {ahorro_txt}te quedas sin stock {'al menos ' if al_final else ''}{E.num(dias_sin)} días "
               "y pierdes "
               f"<b>{perdida_txt}</b>.{vuelta}")

    with st.expander("Ver comparación completa", icon=":material/table:"):
        filas_c = [
            ("Compra", f"{E.num(ev['compra_sug'])} u.", f"{E.num(ev['compra_tuya'])} u."),
            ("Fecha de la compra", f"{f0:%d/%m/%Y}", f"{pr['fecha'].iloc[t_c]:%d/%m/%Y}"),
            ("Llega", "", ""),
            ("Días sin stock", E.num(ev["quiebres_sug"] * dias_p), E.num(ev["quiebres_tuyo"] * dias_p)),
            ("Unidades sin vender", E.num(ev["perdida_sug"]), E.num(ev["perdida_tuya"])),
            ("Inventario promedio", f"{E.num(ev['inv_prom_sug'])} u.", f"{E.num(ev['inv_prom_tuyo'])} u."),
        ]
        lle_s = sim_sug[(sim_sug["llegada"] > 0) & (sim_sug["fecha"] > f0)]
        lle_t = sim_tuyo[(sim_tuyo["llegada"] > 0) & (sim_tuyo["fecha"] > pr["fecha"].iloc[t_c])]
        filas_c[2] = ("Llega", f"{lle_s['fecha'].iloc[0]:%d/%m/%Y}" if len(lle_s) else "—",
                      f"{lle_t['fecha'].iloc[0]:%d/%m/%Y}" if len(lle_t) else "—")
        if costo:
            filas_c.append(("Gasto en esta compra", E.clp(ev["gasto_sug"]), E.clp(ev["gasto_tuyo"])))
        if precio:
            filas_c.append(("Ventas perdidas", E.clp(ev["perdida_sug"] * precio), E.clp(ev["perdida_tuya"] * precio)))
        st.dataframe(pd.DataFrame(filas_c, columns=["", "Sugerencia del modelo", "Tu plan"]),
                     hide_index=True, width="stretch")
        st.caption(f"Después de tu compra, el modelo vuelve a comprar solo desde el "
                   f"{pr['fecha'].iloc[min(auto_desde, len(pr) - 1)]:%d/%m/%Y} (siguiente revisión). "
                   "El ahorro es de caja: lo que no compras ahora se compra más adelante. La venta perdida no vuelve.")

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

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import estilo as E
import sesion as S
from motor import politica as P

dp, res = S.requiere_pronostico()
fi = dp.freq_info
nom = S.mayus(S.nombre_entidad(dp))
dias_p = fi["dias"]
clave = S.clave_dataset(dp)
# horizonte máximo: la fecha del próximo pedido no depende del largo elegido y alcanzan a verse las llegadas
fut = S.pronostico(res.plan.horizonte_max)
entidades = list(fut)

# ---------------------------------------------------------------- parámetros por producto
tiene_lt, tiene_inv = dp.tiene("lead_time"), dp.tiene("inventario")
guardada = st.session_state.get("politica_guardada")
guardada = guardada if guardada and guardada.get("clave") == clave else None
previa = {f["entidad"]: f for f in (guardada or {}).get("tabla", [])}
ajustes = st.session_state.setdefault("dec_ajustes", {}).setdefault(clave, {})
param = {}
for e in entidades:
    g = dp.df[dp.df["entidad"] == e]
    lt = round(P.lead_time_dataset(g["lead_time"]), 1) if tiene_lt else 14.0
    inv = float(g["inventario"].iloc[-1]) if tiene_inv else np.nan
    if e in previa:
        lt = previa[e].get("lead_time", lt) if previa[e].get("lead_time") is not None else lt
        inv = previa[e].get("inventario", inv) if previa[e].get("inventario") is not None else inv
    lt, inv = ajustes.get(e, {}).get("lt", lt), ajustes.get(e, {}).get("inv", inv)
    param[e] = {"lt": float(lt), "inv": inv}

vista = S.vista("decisiones")
E.titulo_compacto("Paso 3 · Decisiones de abastecimiento", vista)

# ---------------------------------------------------------------- producto y panel de control (sobre el gráfico)
ent = S.selector_entidad(entidades, dp, key="dec")
with st.container(border=True, key="panel_control_dec"):
    c1, c2, c3, c4 = st.columns(4, vertical_alignment="bottom")
    nivel = c1.select_slider("Nivel de servicio", list(P.Z_NIVEL), value=(guardada or {}).get("nivel", "90%"),
                             key="dec_nivel", help="Probabilidad de no quedarse sin stock mientras llega un pedido.")
    rev_def = {"D": 30, "W": 28, "M": 30, "Q": 91}[dp.config.frecuencia]
    revision = c2.number_input("Días que cubre cada pedido", 1, 365, int((guardada or {}).get("revision", rev_def)),
                               key="dec_revision",
                               help="Como mínimo se usa el lead time + 20%, para que llegue un pedido antes de "
                                    "necesitar el siguiente.")
    lt_e = c3.number_input(f"Lead time de {ent} (días)", 0.0, 365.0, param[ent]["lt"], 1.0, format="%.1f",
                           key=f"lt_{clave}_{ent}")
    inv_e = c4.number_input(f"Inventario actual de {ent}", 0.0, None,
                            None if pd.isna(param[ent]["inv"]) else float(param[ent]["inv"]), 100.0,
                            format="%.0f", key=f"inv_{clave}_{ent}", placeholder="Ingrésalo")
    if not tiene_lt or not tiene_inv:
        st.caption(":material/info: Tu archivo no trae " + " ni ".join(
            x for x, falta in (("lead time", not tiene_lt), ("inventario", not tiene_inv)) if falta)
            + ": complétalo aquí para cada " + S.nombre_entidad(dp) + ".")
    panel = st.container()
ajustes[ent] = {"lt": float(lt_e), "inv": np.nan if inv_e is None else float(inv_e)}
param[ent] = {"lt": float(lt_e), "inv": ajustes[ent]["inv"]}

tabla_pol = pd.DataFrame([{"entidad": e, "lead_time": v["lt"], "inventario": v["inv"]} for e, v in param.items()])
st.session_state["politica"] = {"clave": clave, "nivel": nivel, "revision": revision, "tabla": tabla_pol}
S.actualizar_registro(politica=S.politica_a_json(tabla_pol, nivel, int(revision)))

# ---------------------------------------------------------------- decisiones de todos los productos
decs = {}
for e, v in param.items():
    par = P.Parametros(lead_time_dias=v["lt"], revision_dias=float(revision), nivel_servicio=nivel,
                       inventario_actual=None if pd.isna(v["inv"]) else float(v["inv"]), errores=S.errores_modelo(e))
    decs[e] = P.decidir(e, fut[e], dp.config.frecuencia, par)
d = decs[ent]
pr = fut[ent]
u = fi["unidad"]

ESTADOS = {"Riesgo de quiebre": "🔴 Riesgo de quiebre", "Pedir ahora": "🟠 Pedir ahora",
           "Stock suficiente": "🟢 Stock suficiente", "Sin inventario": "⚪ Falta inventario"}
orden = {"Riesgo de quiebre": 0, "Pedir ahora": 1, "Stock suficiente": 2, "Sin inventario": 3}
filas = []
for e, de in sorted(decs.items(), key=lambda x: (orden[x[1].estado], str(x[0]))):
    filas.append({
        nom: e, "Estado": ESTADOS[de.estado], "Inventario": de.inventario, "Stock de seguridad": de.ss,
        "Punto de reorden hoy": de.rop, "Meta (T) hoy": de.meta,
        "Próximo pedido (u.)": de.cantidad if de.estado != "Sin inventario" else np.nan,
        "Fecha del próximo pedido": de.fecha_pedido.strftime("%d/%m/%Y") if de.fecha_pedido is not None else
        ("—" if de.estado != "Sin inventario" else ""),
        "Cobertura (días)": de.cobertura_dias,
    })
tabla = pd.DataFrame(filas)

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
    fig.update_layout(title=f"Inventario proyectado · {ent}", yaxis_title="Unidades", height=440, margin=dict(t=80))
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
    fig.update_layout(title=f"Tu plan vs. la sugerencia del modelo · {ent}", yaxis_title="Unidades", height=440,
                      margin=dict(t=80))
    return fig



def tabla_skus():
    with st.container():
        vista = tabla[[nom, "Estado", "Fecha del próximo pedido", "Próximo pedido (u.)", "Cobertura (días)"]].rename(
            columns={"Próximo pedido (u.)": "Cantidad (u.)", "Cobertura (días)": "Alcanza (días)",
                     "Fecha del próximo pedido": "Próximo pedido"})
        for c in ["Cantidad (u.)", "Alcanza (días)"]:
            vista[c] = vista[c].map(lambda v: "" if pd.isna(v) else E.num(v))
        st.dataframe(E.destacar_fila(vista, nom, ent), width="stretch", hide_index=True,
                     height=min(520, 38 + 35 * len(vista)))


def detalle_tecnico():
    with st.container(border=True):
        t1, t2, t3 = st.columns(3)
        t1.metric("Stock de seguridad (SS) hoy", E.num(d.ss))
        t2.metric("Punto de reorden (ROP) hoy", E.num(d.rop))
        t3.metric("Meta (T) hoy", E.num(d.meta))
        st.latex(r"SS = Z \cdot \sigma_L \qquad ROP = \sum_{L} d + SS \qquad T = \sum_{\max(P,\,1{,}2L)} d + SS")
        st.caption(f"σ_L = {E.num(d.sigma_lt)} u.: error real acumulado del modelo en el lead time (la regla σ·√L "
                   f"daría {E.num(d.sigma * np.sqrt(d.L))}). Demanda {E.num(d.d, 1)} por {u} · lead time "
                   f"{E.num(d.L * dias_p, 1)} días · cada pedido cubre {E.num(P.ciclo(d.P, d.L) * dias_p)} días · "
                   f"nivel de servicio {nivel}.")
        if d.aviso:
            st.caption(":material/warning: " + d.aviso)


# ---------------------------------------------------------------- visualización
if vista == "Todos los productos":
    tabla_skus()
    S.panel_dataset(compacto=True)
    st.stop()
if d.inventario is None:
    E.nota(f"Ingresa el inventario actual de <b>{ent}</b> en el panel de arriba para ver la proyección "
           "y la fecha del próximo pedido.")
    S.panel_dataset(compacto=True)
    st.stop()

sim_sug = P.simular(pr, d)
sim_tuyo, ev = None, None
cc_ent = S.costo_compra(ent)

if vista in ("Inventario proyectado", "Detalle técnico"):
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Inventario actual", f"{E.num(d.inventario)} u.",
              help=(f"Capital: {E.clp_md(d.inventario * cc_ent)}" if cc_ent else None))
    if d.fecha_pedido is None:
        m2.metric("Próximo pedido", "—")
    else:
        m2.metric("Próximo pedido", "Hoy" if d.estado in ("Pedir ahora", "Riesgo de quiebre") else f"{d.fecha_pedido:%d/%m/%Y}")
    m3.metric("Cantidad sugerida", f"{E.num(d.cantidad)} u." if d.fecha_pedido is not None else "—")
    m4.metric("Te alcanza para", f"{E.num(d.cobertura_dias)} días" if d.cobertura_dias is not None else "—")
    if d.estado == "Riesgo de quiebre":
        E.nota(f"🔴 <b>Pide hoy {E.num(d.cantidad)} u.</b> El inventario ya está bajo el stock de seguridad.")
    elif d.estado == "Pedir ahora":
        E.nota(f"🟠 <b>Pide hoy {E.num(d.cantidad)} u.</b>")
    elif d.fecha_pedido is not None:
        lle = sim_sug[(sim_sug["llegada"] > 0) & (sim_sug["fecha"] > d.fecha_pedido)]
        E.nota(f"🟢 <b>No necesitas pedir todavía.</b> El modelo pedirá <b>{E.num(d.cantidad)} u.</b> el "
               f"<b>{d.fecha_pedido:%d/%m/%Y}</b>" + (f", que llegan el {lle['fecha'].iloc[0]:%d/%m/%Y}." if len(lle) else "."))
    else:
        E.nota(f"🟢 <b>No necesitas pedir.</b> El inventario alcanza para los {E.num(len(pr) * dias_p)} días analizados.")
    if vista == "Detalle técnico":
        detalle_tecnico()
    else:
        E.grafico(fig_automatico(sim_sug), key="fig_inv")
        st.caption("Cuando la bodega toca el punto de reorden (ROP) se pide. Mientras llega, baja hasta cerca del stock "
                   "de seguridad (SS); al llegar, sube hasta la meta (T).")
else:
    precio = S.precio(ent)
    sug_ped = sim_sug[sim_sug["pedido"] > 0]
    f0 = sug_ped["fecha"].iloc[0] if len(sug_ped) else pr["fecha"].iloc[0]
    q0 = float(sug_ped["pedido"].iloc[0]) if len(sug_ped) else 0.0
    clave_plan = f"{clave}_{ent}"
    with panel:
        costo = cc_ent
        t1, t2, t3, t4 = st.columns(4, vertical_alignment="bottom")
        with t1:
            por = S.elegir_uno("Defino la compra en", ["Unidades", "Pesos ($)"], key="por",
                               estado="plan_por") if costo else "Unidades"
        if por == "Unidades":
            q = t2.number_input("Cantidad a comprar (u.)", 0.0, None, float(round(q0)),
                                100.0, format="%.0f", key=f"q_{clave_plan}")
        else:
            monto = t2.number_input("Monto a gastar ($)", 0.0, None,
                                    float(round(q0 * costo)), 10000.0, format="%.0f", key=f"m_{clave_plan}")
            q = float(np.floor(monto / costo))
        fecha_c = t3.date_input("Fecha de la compra", f0.date(), min_value=pr["fecha"].iloc[0].date(),
                                max_value=pr["fecha"].iloc[-1].date(), format="DD/MM/YYYY", key=f"fecha_{clave_plan}")
        lt_c = t4.number_input("Lead time de esta compra", 0.0, 365.0, float(round(d.L * dias_p, 1)), 1.0,
                               format="%.1f", key=f"ltc_{clave_plan}",
                               help="Cámbialo para simular, por ejemplo, una compra urgente.")

    t_c = int(np.searchsorted(pr["fecha"].dt.normalize().to_numpy(), np.datetime64(pd.Timestamp(fecha_c))))
    t_c = min(t_c, len(pr) - 1)
    auto_desde = t_c + int(np.ceil(P.ciclo(d.P, d.L)))
    sim_tuyo = P.simular(pr, d, manuales={t_c: (q, lt_c / dias_p)}, auto_desde=auto_desde)
    ev = P.evaluar_plan(sim_sug, sim_tuyo, auto_desde, precio=precio, costo=costo, dias_periodo=dias_p)

    dif_q = ev["compra_sug"] - ev["compra_tuya"]
    parecido = abs(dif_q) <= max(0.01 * ev["compra_sug"], 1.0) and ev["perdida_extra"] <= 0
    dias_sin = max(0, ev["quiebres_tuyo"] - ev["quiebres_sug"]) * dias_p
    al_final = ev["ultimo_quiebre"] is not None and ev["ultimo_quiebre"] >= pr["fecha"].iloc[-1]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Tu compra", f"{E.num(q)} u.", delta=f"sugerido {E.num(q0)} u.", delta_color="off", delta_arrow="off")
    if costo:
        ahorro = ev["ahorro_caja"]
        k2.metric("Ahorras en esta compra" if (ahorro >= 0 or parecido) else "Gastas de más",
                  E.clp_corto(abs(ahorro)) if not parecido else "$0")
    else:
        k2.metric("Compras de menos" if (dif_q >= 0 or parecido) else "Compras de más",
                  f"{E.num(abs(dif_q))} u." if not parecido else "0 u.")
    if precio:
        k3.metric("Pierdes en ventas", E.clp_corto(ev["ventas_perdidas"]),
                  help=f"{E.num(ev['perdida_extra'])} u. que no podrás vender por falta de stock.")
    else:
        k3.metric("Unidades que no vendes", f"{E.num(ev['perdida_extra'])} u.")
    k4.metric("Días sin stock", (("≥ " if al_final else "") + E.num(dias_sin)) if ev["perdida_extra"] > 0 else "0")

    if parecido:
        E.nota("⚪ <b>Es prácticamente lo mismo que sugiere el modelo.</b>")
    elif ev["perdida_extra"] <= 0:
        E.nota(f"🟢 <b>Buena decisión:</b> compras {E.num(ev['compra_tuya'])} u. en vez de {E.num(ev['compra_sug'])}"
               + (f" y ahorras <b>{E.clp(ev['ahorro_caja'])}</b>" if costo and ev["ahorro_caja"] > 0 else "")
               + " sin quedarte sin stock.")
    else:
        perdida_txt = E.clp(ev["ventas_perdidas"]) + " en ventas" if precio else f"{E.num(ev['perdida_extra'])} u."
        ahorro_txt = f"ahorras {E.clp(ev['ahorro_caja'])}, pero " if costo and ev["ahorro_caja"] > 0 else ""
        if ev["ultimo_quiebre"] is not None and not al_final:
            vuelta = f" Te recuperas el <b>{ev['ultimo_quiebre'] + pd.Timedelta(days=dias_p):%d/%m/%Y}</b>."
        else:
            auto = sim_tuyo[(sim_tuyo["pedido"] > 0) & (sim_tuyo["pedido_manual"] <= 0) & (sim_tuyo.index >= auto_desde)]
            vuelta = (f" Te recuperas cerca del <b>{auto['fecha'].iloc[0] + pd.Timedelta(days=float(np.ceil(d.L)) * dias_p):%d/%m/%Y}</b>."
                      if len(auto) else " No alcanzas a recuperarte dentro del período analizado.")
        E.nota(f"🔴 <b>Ojo:</b> {ahorro_txt}te quedas sin stock {'al menos ' if al_final else ''}{E.num(dias_sin)} días "
               f"y pierdes <b>{perdida_txt}</b>.{vuelta}")
    E.grafico(fig_comparar(sim_sug, sim_tuyo), key="fig_tuyo")

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
with st.container(horizontal=True, horizontal_alignment="right"):
    st.download_button("Descargar decisiones (Excel)", E.excel_bytes(hojas), file_name="decisiones_abastecimiento.xlsx",
                       icon=":material/download:", type="tertiary",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
S.panel_dataset(compacto=True)

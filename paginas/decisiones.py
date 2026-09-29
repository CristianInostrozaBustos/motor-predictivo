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
        "Punto de reorden": d.rop,
        "Meta (T)": d.meta,
        "Cantidad a pedir": d.cantidad if d.estado != "Sin inventario" else np.nan,
        "Fecha de pedido": d.fecha_pedido.strftime("%d/%m/%Y") if d.fecha_pedido is not None else
        ("—" if d.estado != "Sin inventario" else ""),
        "Cobertura (días)": d.cobertura_dias,
    })
tabla = pd.DataFrame(filas)
st.dataframe(tabla, width="stretch", hide_index=True, column_config={
    c: st.column_config.NumberColumn(format="%.0f") for c in
    ["Inventario", "Stock de seguridad", "Punto de reorden", "Meta (T)", "Cantidad a pedir", "Cobertura (días)"]
})

# ---------------------------------------------------------------- detalle
st.write("")
st.markdown(f"## Detalle por {S.nombre_entidad(dp)}")
ent = S.selector_entidad(entidades, dp, key="dec")
d = decs[ent]
pr = fut[ent]
u = fi["unidad"]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Stock de seguridad", E.num(d.ss))
m2.metric("Punto de reorden", E.num(d.rop))
m3.metric("Meta (T)", E.num(d.meta))
m4.metric("Inventario actual", E.num(d.inventario) if d.inventario is not None else "—")
col_t, col_m = st.columns([1.5, 1], gap="large")
with col_m:
    if d.estado == "Sin inventario":
        mensaje = f"Ingresa el inventario actual de <b>{ent}</b> para saber si hay que pedir."
    elif d.estado == "Riesgo de quiebre":
        mensaje = (f"<b>{ent}</b> tiene {E.num(d.inventario)} unidades, bajo el stock de seguridad. "
                   f"Pide <b>{E.num(d.cantidad)} unidades</b> hoy; es probable un quiebre antes de que llegue.")
    elif d.estado == "Pedir ahora":
        mensaje = (f"<b>{ent}</b> tiene {E.num(d.inventario)} unidades, bajo el punto de reorden ({E.num(d.rop)}). "
                   f"Pide <b>{E.num(d.cantidad)} unidades</b> hoy para llegar a la meta.")
    elif d.fecha_pedido is not None:
        mensaje = (f"<b>{ent}</b> tiene stock para unos {E.num(d.cobertura_dias)} días. Según el pronóstico, "
                   f"el próximo pedido será el <b>{d.fecha_pedido:%d/%m/%Y}</b>, por unas <b>{E.num(d.cantidad)} unidades</b>.")
    else:
        mensaje = (f"<b>{ent}</b> tiene stock para unos {E.num(d.cobertura_dias)} días: no necesita pedido dentro "
                   f"de los {H} {fi['unidad_pl']} pronosticados.")
    E.nota(mensaje)
    st.caption(f"Demanda pronosticada {E.num(d.d, 1)} por {u} · incertidumbre σ = {E.num(d.sigma, 1)} · "
               f"lead time {E.num(d.L, 1)} {fi['unidad_pl']} · revisión {E.num(d.P, 1)} {fi['unidad_pl']} · "
               f"nivel de servicio {nivel}.")
    if d.aviso:
        st.caption(":material/warning: " + d.aviso)
    with st.expander("Cómo se calcula", icon=":material/function:"):
        st.latex(r"SS = Z \cdot \sigma \cdot \sqrt{L} \qquad ROP = d \cdot L + SS \qquad T = d\,(L + P) + SS")
        st.caption("d y σ salen del pronóstico para los períodos que cubre el pedido (L + P); σ se obtiene del ancho "
                   "del rango P10–P90. Si el inventario baja del punto de reorden, se pide lo necesario para llegar a T.")

with col_t:
    # la proyección se alarga hasta que llegue el próximo pedido (si no, se vería bajar sin reponerse nunca)
    if d.periodos_hasta_pedido is not None:
        H_sim = int(np.ceil(d.periodos_hasta_pedido + d.L + 0.5 * d.P)) + 3
    else:
        H_sim = res.plan.horizonte_max
    H_sim = min(res.plan.horizonte_max, max(len(pr), H_sim))
    pr_sim = S.pronostico(H_sim)[ent] if H_sim > len(pr) else pr
    sim = P.simular(pr_sim, d)
    if not len(sim):
        E.nota("Ingresa el inventario actual en <b>Lead time e inventario</b> (arriba) para ver la proyección "
               "del inventario y la fecha del próximo pedido.")
    else:
        with st.container(border=True):
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=sim["fecha"], y=sim["inventario"], name="Inventario proyectado", mode="lines",
                                     line=dict(color=E.AZUL, width=2.2), hovertemplate="%{y:,.0f}"))
            ped = sim[sim["pedido"] > 0]
            fig.add_trace(go.Scatter(x=ped["fecha"], y=ped["inventario"], mode="markers", name="Se emite pedido",
                                     marker=dict(color=E.NARANJO, size=10, symbol="triangle-up",
                                                 line=dict(color="white", width=1.5)),
                                     customdata=ped["pedido"], hovertemplate="pedido de %{customdata:,.0f} u."))
            lle = sim[sim["llegada"] > 0]
            fig.add_trace(go.Scatter(x=lle["fecha"], y=lle["inventario"], mode="markers", name="Llega el pedido",
                                     marker=dict(color=E.VERDE, size=10, symbol="diamond",
                                                 line=dict(color="white", width=1.5)),
                                     customdata=lle["llegada"], hovertemplate="llegan %{customdata:,.0f} u."))
            # tramo en que el pedido va en camino (entrada de leyenda con un cuadrado del mismo color)
            fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name="Pedido en camino",
                                     marker=dict(symbol="square", size=12, color=E.NARANJO, opacity=0.25)))
            for _, fp in ped.iterrows():
                llegadas = lle[lle["fecha"] > fp["fecha"]]
                if len(llegadas):
                    fig.add_vrect(x0=fp["fecha"], x1=llegadas["fecha"].iloc[0], fillcolor=E.NARANJO, opacity=0.07,
                                  line_width=0)
            fig.add_trace(go.Scatter(x=sim["fecha"], y=sim["rop"], name="Punto de reorden", mode="lines",
                                     line=dict(color=E.NARANJO, dash="dash", width=1.5), hovertemplate="%{y:,.0f}"))
            fig.add_trace(go.Scatter(x=sim["fecha"], y=sim["ss"], name="Stock de seguridad", mode="lines",
                                     line=dict(color=E.ROJO, dash="dot", width=1.5), hovertemplate="%{y:,.0f}"))
            fig.update_layout(title=f"Inventario proyectado · {ent}", yaxis_title="Unidades", height=440,
                              margin=dict(t=120))
            E.grafico(fig, key="fig_inv")
            texto = ("Proyección aplicando la política sobre el pronóstico. El punto de reorden se recalcula cada período "
                     "con la demanda que viene.")
            if len(ped):
                f_ped = ped["fecha"].iloc[0]
                llegadas = lle[lle["fecha"] > f_ped]
                if len(llegadas):
                    texto += (f" El pedido del {f_ped:%d/%m/%Y} llega el {llegadas['fecha'].iloc[0]:%d/%m/%Y} "
                              f"(lead time de {E.num(d.L * fi['dias'])} días): mientras va en camino (zona sombreada) el "
                              "inventario sigue bajando, y para eso existe el punto de reorden.")
            st.caption(texto)

# ---------------------------------------------------------------- descarga
exp = tabla.copy()
exp["Estado"] = exp["Estado"].str[2:]
st.download_button("Descargar decisiones (Excel)", E.excel_bytes({"Decisiones": exp.round(1)}),
                   file_name="decisiones_abastecimiento.xlsx", icon=":material/download:",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

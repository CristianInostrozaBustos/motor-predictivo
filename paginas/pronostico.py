import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import estilo as E
import sesion as S

dp, res = S.requiere_pronostico()
S.panel_dataset()
fut = S.pronostico()
entidades = list(fut)
fi = dp.freq_info
H = S.horizonte()
obj = dp.etiquetas["objetivo"]

E.encabezado(
    "Paso 2",
    "Pronóstico",
    f"{obj} esperado para los próximos {H} {fi['unidad_pl']}, con el rango en que probablemente se moverá.",
)

# ---------------------------------------------------------------- qué ver
with st.container(border=True):
    sel = S.selector_vista(entidades, dp, key="pron")
    ver = entidades if sel is None else sel
    comparar = sel is not None and len(sel) > 1
    if sel is None:
        nombre_vista = (f"todos los {S.nombre_entidad(dp, True)} (total)" if len(entidades) > 1 else str(entidades[0]))
    else:
        nombre_vista = S.titulo_seleccion(sel, dp)

    # ------------------------------------------------------------ datos de la vista
    base = dp.df[dp.df["entidad"].isin(ver)]
    hist = base.groupby("fecha", as_index=False)["objetivo"].sum()
    pr = pd.concat([fut[e] for e in ver]).groupby("fecha", as_index=False)[["P10", "P50", "P90"]].sum()
    anterior = hist["objetivo"].tail(H).sum()
    total_p50 = pr["P50"].sum()
    var = (total_p50 - anterior) / anterior * 100 if anterior > 0 else None

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"Total esperado ({H} {fi['unidad_pl']})", E.num(total_p50))
    c2.metric(f"Promedio por {fi['unidad']}", E.num(pr["P50"].mean()))
    c3.metric(f"Vs. últimos {H} {fi['unidad_pl']}", E.pct(var) if var is not None else "—",
              delta=("sube" if var > 0 else "baja") if var is not None else None,
              delta_color="off", delta_arrow="off")
    c4.metric("Escenario alto (P90)", E.num(pr["P90"].sum()), delta=f"bajo (P10): {E.num(pr['P10'].sum())}",
              delta_color="off", delta_arrow="off",
              help="Rango probable del total entre el escenario bajo y el alto. Al sumar varios, es una aproximación (suma de rangos).")

    atras = min(len(hist), max(3 * H, {"D": 120, "W": 52, "M": 36, "Q": 12}[dp.config.frecuencia]))
    desde = hist["fecha"].iloc[-atras]
    if not comparar:
        h_ver = hist.tail(atras)
        fig = E.fig_banda(pr["fecha"], pr["P10"], pr["P50"], pr["P90"], nombre_banda="Rango probable (P10–P90)",
                          nombre_p50="Pronóstico")
        fig.add_trace(go.Scatter(x=h_ver["fecha"], y=h_ver["objetivo"], name="Historial",
                                 line=dict(color=E.TINTA, width=1.5), hovertemplate="%{y:,.0f}"))
    else:
        fig = go.Figure()
        for e in sel:
            col = E.color_sku(e, entidades)
            g = dp.df[(dp.df["entidad"] == e) & (dp.df["fecha"] >= desde)]
            fig.add_trace(go.Scatter(x=g["fecha"], y=g["objetivo"], name=str(e), legendgroup=str(e), mode="lines",
                                     line=dict(color=col, width=1.3), opacity=0.55, hovertemplate="%{y:,.0f}"))
            f = fut[e]
            fig.add_trace(go.Scatter(x=f["fecha"], y=f["P50"], name=f"{e} (pronóstico)", legendgroup=str(e),
                                     showlegend=False, mode="lines", line=dict(color=col, width=2.4),
                                     hovertemplate="%{y:,.0f} (pronóstico)"))
    fig.add_vline(x=hist["fecha"].iloc[-1], line=dict(color=E.EJE, width=1, dash="dot"))
    fig.update_layout(title=f"{obj} · {nombre_vista}", yaxis_title=obj, height=460)
    E.grafico(fig, key="fig_pron")
    if comparar:
        st.caption("Línea tenue: historial. Línea gruesa, a la derecha de la línea punteada: pronóstico. "
                   "Para ver el rango de incertidumbre, elige un solo elemento o la vista Total.")
    supuestos = []
    if dp.tiene("precio"):
        supuestos.append("el precio se mantiene en su último valor")
    if dp.tiene("promocion"):
        supuestos.append("no hay promociones")
    if not comparar:
        st.caption("El pronóstico (azul) es el valor más probable. En 8 de cada 10 períodos la realidad debería caer dentro de la banda."
                   + (" Supone que " + " y ".join(supuestos) + "." if supuestos else ""))

# ---------------------------------------------------------------- vistas opcionales
st.write("")
vistas = st.pills(
    "Qué más quieres ver",
    ["Tabla del pronóstico", "Qué tan preciso es", "Prueba con datos pasados"],
    selection_mode="multi", default=["Tabla del pronóstico"], key="vistas_pron",
)

if "Tabla del pronóstico" in (vistas or []):
    st.markdown(f"### Tabla del pronóstico · {nombre_vista}")
    nom_col = S.mayus(S.nombre_entidad(dp))
    if comparar:
        tabla = pd.concat([fut[e].assign(entidad=e) for e in sel])
    else:
        tabla = pr.copy()
    tabla["fecha"] = tabla["fecha"].dt.strftime("%d/%m/%Y")
    tabla = tabla.rename(columns={"fecha": "Fecha", "entidad": nom_col, "P50": "Pronóstico", "P10": "Escenario bajo (P10)",
                                  "P90": "Escenario alto (P90)"})
    tabla = tabla[([nom_col] if comparar else []) + ["Fecha", "Pronóstico", "Escenario bajo (P10)", "Escenario alto (P90)"]]
    st.dataframe(tabla, width="stretch", hide_index=True, height=min(420, 38 + 35 * len(tabla)),
                 column_config={c: st.column_config.NumberColumn(format="%.0f") for c in tabla.columns[-3:]})

met = res.metricas_entidad.set_index("entidad")
if "Qué tan preciso es" in (vistas or []):
    st.markdown(f"### Qué tan preciso es · {nombre_vista}")
    st.caption(f"Medido pronosticando los últimos {res.plan.validacion} {fi['unidad_pl']} de tu historial como si "
               "no los conociéramos, y comparando con lo que realmente pasó.")
    if len(ver) > 1:
        m = met.loc[[e for e in ver if e in met.index]]
        wape = (m["wape"] * 1).mean()
        c1, c2, c3 = st.columns(3)
        c1.metric("Error promedio", E.pct(wape), help="Promedio entre entidades del error absoluto sobre el total real (WAPE).")
        c2.metric("Aciertos dentro del rango", E.pct(m["cobertura"].mean()))
        c3.metric("Mejora vs. repetir la temporada anterior", E.pct((m["wape_naive"].mean() - wape) / m["wape_naive"].mean() * 100))
        vista = pd.DataFrame({
            S.mayus(S.nombre_entidad(dp)): m.index,
            "Error": m["wape"].map(lambda v: E.pct(v)),
            "Dentro del rango": m["cobertura"].map(lambda v: E.pct(v)),
            "Error sin modelo": m["wape_naive"].map(lambda v: E.pct(v)),
        })
        st.dataframe(vista, width="stretch", hide_index=True)
    else:
        m = met.loc[ver[0]]
        c1, c2, c3 = st.columns(3)
        c1.metric("Error promedio", E.pct(m["wape"]),
                  help="Suma de los errores absolutos dividida por la demanda real total (WAPE).")
        c2.metric("Aciertos dentro del rango", E.pct(m["cobertura"]))
        mejora = (m["wape_naive"] - m["wape"]) / m["wape_naive"] * 100
        c3.metric("Mejora vs. repetir la temporada anterior", E.pct(mejora),
                  delta="mejor" if mejora > 0 else "peor", delta_color="normal" if mejora > 0 else "inverse")

if "Prueba con datos pasados" in (vistas or []):
    st.markdown("### Prueba con datos pasados")
    con_bt = [e for e in ver if e in res.backtest]
    SUMA = f"Suma de los {len(con_bt)} seleccionados" if sel is not None else f"Total de {S.nombre_entidad(dp, True)}"
    if len(con_bt) > 1:
        prueba = S.elegir_uno("Ver la prueba de", con_bt + [SUMA], key="bt", estado="bt_ent",
                              defecto=con_bt[0] if sel is not None else SUMA)
    else:
        prueba = con_bt[0]
    if prueba == SUMA:
        bt = pd.concat([res.backtest[e] for e in con_bt]).groupby(
            "fecha", as_index=False)[["real", "P10", "P50", "P90", "naive"]].sum()
    else:
        bt = res.backtest[prueba]
    temporada = {"D": "la última semana", "W": "el mismo período del año anterior",
                 "M": "el mismo mes del año anterior", "Q": "el mismo trimestre del año anterior"}[dp.config.frecuencia]
    with st.container(border=True):
        fig = E.fig_banda(bt["fecha"], bt["P10"], bt["P50"], bt["P90"], nombre_p50="Lo que pronosticó")
        fig.add_trace(go.Scatter(x=bt["fecha"], y=bt["real"], name="Lo que pasó", line=dict(color=E.TINTA, width=1.8),
                                 hovertemplate="%{y:,.0f}"))
        fig.add_trace(go.Scatter(x=bt["fecha"], y=bt["naive"], name="Sin modelo (repetir " + temporada + ")",
                                 line=dict(color=E.NARANJO, width=1.4, dash="dot"), hovertemplate="%{y:,.0f}"))
        fig.update_layout(title=f"Pronóstico vs. realidad · {prueba} · últimos {res.plan.validacion} {fi['unidad_pl']}",
                          height=400)
        E.grafico(fig, key="fig_bt")
        st.caption(f"Se escondieron los últimos {res.plan.validacion} {fi['unidad_pl']} del historial, el modelo los "
                   "pronosticó sin verlos y aquí se compara con lo que realmente pasó. La línea punteada naranja es la "
                   f"referencia sin modelo: repetir {temporada} conocida antes de la prueba. Si la línea azul queda "
                   "más cerca de la negra que la naranja, el modelo aporta.")

# ---------------------------------------------------------------- descarga
todo = pd.concat([f.assign(entidad=e) for e, f in fut.items()])
todo = todo[["entidad", "fecha", "P50", "P10", "P90"]].rename(columns={
    "entidad": S.mayus(S.nombre_entidad(dp)), "fecha": "Fecha", "P50": "Pronóstico",
    "P10": "Escenario bajo (P10)", "P90": "Escenario alto (P90)"}).round(1)
todo["Fecha"] = todo["Fecha"].dt.date
st.download_button("Descargar pronóstico (Excel)", E.excel_bytes({"Pronóstico": todo}),
                   file_name="pronostico.xlsx", icon=":material/download:",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import estilo as E
import sesion as S

from generar import bloque_horizonte
from motor import reglas as R

dp = st.session_state.get("dp")
if dp is None:
    S.requiere_pronostico()
vista = S.vista("pronostico")
E.titulo_compacto("Pronóstico", S.etiqueta_vista(vista) if vista else "¿Cuánto quieres pronosticar?")
S.aviso_datos_nuevos()
plan = R.planificar(dp)
if not plan.viable:
    S.panel_dataset()
    st.error(f"El historial es demasiado corto para pronosticar: se necesitan al menos "
             f"{R.minimo_registros(dp.config.frecuencia)} {dp.freq_info['unidad_pl']} por {S.nombre_entidad(dp)}.")
    st.stop()
bloque_horizonte(dp, plan)
dp, res = S.resultado()
S.panel_dataset()
if res is None:
    st.stop()
fut = S.pronostico()
entidades = list(fut)
fi = dp.freq_info
H = S.horizonte()
obj = dp.etiquetas["objetivo"]
nom_col = S.mayus(S.nombre_entidad(dp))
met = res.metricas_entidad.set_index("entidad")

if vista == "Prueba con datos pasados":
    con_bt = [e for e in entidades if e in res.backtest]
    prueba = S.selector_entidad(con_bt, dp, key="bt")
    bt = res.backtest[prueba]
    temporada = {"D": "la última semana", "W": "el mismo período del año anterior",
                 "M": "el mismo mes del año anterior", "Q": "el mismo trimestre del año anterior"}[dp.config.frecuencia]
    m = met.loc[prueba] if prueba in met.index else None
    if m is not None:
        c1, c2, c3 = st.columns(3)
        c1.metric("Error promedio", E.pct(m["wape"]),
                  help="Suma de los errores absolutos dividida por la demanda real total (WAPE).")
        c2.metric("Aciertos dentro del rango", E.pct(m["cobertura"]))
        mejora = (m["wape_naive"] - m["wape"]) / m["wape_naive"] * 100 if m["wape_naive"] > 0 else np.nan
        if np.isfinite(mejora):
            c3.metric("Mejora vs. sin modelo", E.pct(mejora),
                      delta="mejor" if mejora > 0 else "peor", delta_color="normal" if mejora > 0 else "inverse")
        else:
            c3.metric("Mejora vs. sin modelo", "—", help="No se puede calcular: en el período de prueba no hubo "
                                                          "demanda o la referencia sin modelo no tuvo error.")
    fig = E.fig_banda(bt["fecha"], bt["P10"], bt["P50"], bt["P90"], nombre_p50="Lo que pronosticó")
    fig.add_trace(go.Scatter(x=bt["fecha"], y=bt["real"], name="Lo que pasó", line=dict(color=E.TINTA, width=1.8),
                             hovertemplate="%{y:,.0f}"))
    fig.add_trace(go.Scatter(x=bt["fecha"], y=bt["naive"], name="Sin modelo (repetir " + temporada + ")",
                             line=dict(color=E.NARANJO, width=1.4, dash="dot"), hovertemplate="%{y:,.0f}"))
    fig.update_layout(title=f"Pronóstico vs. realidad · {prueba} · últimos {res.plan.validacion} {fi['unidad_pl']}",
                      height=440)
    E.grafico(fig, key="fig_bt")
    st.caption(f"Se escondieron los últimos {res.plan.validacion} {fi['unidad_pl']} del historial, el modelo los "
               "pronosticó sin verlos y aquí se compara con lo que realmente pasó. La línea punteada naranja es la "
               f"referencia sin modelo: repetir {temporada} conocida antes de la prueba. Si la línea azul queda "
               "más cerca de la negra que la naranja, el modelo aporta.")
else:
    sel = S.selector_vista(entidades, dp, key="pron")
    ver = list(sel)

    # ------------------------------------------------------------ panel de control
    en_pesos = False
    if vista != "Precisión" and S.hay_precios():
        with st.container(border=True, key="panel_control_pron"):
            c1, _ = st.columns([1, 3])
            with c1:
                en_pesos = S.elegir_uno("Ver en", ["Unidades", "Pesos ($)"], key="unid_pron",
                                        estado="ver_en") == "Pesos ($)"
    if en_pesos:
        precios_v = {e: S.precio(e) for e in ver}
        sin_p = [e for e in ver if not precios_v[e]]
        ver = [e for e in ver if precios_v[e]] or ver
        if sin_p:
            st.caption(f":material/info: {len(sin_p)} sin precio no se incluyen en la vista en pesos "
                       "(asígnalo en Finanzas → Precio y costo).")
        infl = {e: S.factor_inflacion(fut[e]["fecha"]) for e in ver}
        maximo = max(float((fut[e]["P90"] * (precios_v.get(e) or 0) * infl[e]).max()) for e in ver)
        div, obj_v, hov = E.escala_pesos(maximo)
        fut_v = {e: fut[e].assign(**{q: fut[e][q] * (precios_v.get(e) or 0) * infl[e] / div
                                     for q in ("P10", "P50", "P90")}) for e in ver}
        st.caption(":material/trending_up: " + S.nota_inflacion())
        df_v = dp.df[dp.df["entidad"].isin(ver)].copy()
        df_v["objetivo"] = df_v["objetivo"] * df_v["entidad"].map(precios_v).fillna(0) / div
        fmt = lambda v, _d=div: E.clp_corto(v * _d)  # noqa: E731
    else:
        fut_v, df_v, obj_v, fmt, hov, div = fut, dp.df, obj, E.num, "%{y:,.0f}", 1.0
    sel = [e for e in sel if e in ver]
    nombre_vista = S.titulo_seleccion(sel, dp)

    hist = pd.DataFrame({"fecha": sorted(df_v.loc[df_v["entidad"].isin(ver), "fecha"].unique())})

    if vista == "Pronóstico":
        # misma vista para uno o varios productos: cada uno con su color, su historial, su pronóstico y su rango
        atras = min(len(hist), max(3 * H, {"D": 120, "W": 52, "M": 36, "Q": 12}[dp.config.frecuencia]))
        desde = hist["fecha"].iloc[-atras]
        fig = go.Figure()
        filas_r = []
        for e in sel:
            col = E.color_sku(e, entidades)
            f = fut_v[e]
            g = df_v[df_v["entidad"] == e]
            g_ver = g[g["fecha"] >= desde]
            fig.add_trace(go.Scatter(x=pd.concat([f["fecha"], f["fecha"][::-1]]),
                                     y=pd.concat([f["P90"], f["P10"][::-1]]), fill="toself",
                                     fillcolor=E.rgba(col, 0.16), line=dict(width=0), hoverinfo="skip",
                                     legendgroup=str(e), showlegend=False))
            fig.add_trace(go.Scatter(x=g_ver["fecha"], y=g_ver["objetivo"], name=str(e), legendgroup=str(e),
                                     mode="lines", line=dict(color=col, width=1.3), opacity=0.55,
                                     hovertemplate=hov + " (historial)"))
            fig.add_trace(go.Scatter(x=f["fecha"], y=f["P50"], name=f"{e} (pronóstico)", legendgroup=str(e),
                                     showlegend=False, mode="lines", line=dict(color=col, width=2.4),
                                     customdata=np.stack([f["P10"], f["P90"]], axis=1),
                                     hovertemplate=hov + " (pronóstico)<br>rango: %{customdata[0]:,.0f} – "
                                                         "%{customdata[1]:,.0f}"))
            h_e = g["objetivo"].tail(H).sum()
            v_e = (f["P50"].sum() - h_e) / h_e * 100 if h_e > 0 else None
            filas_r.append({nom_col: e, f"Esperado ({H} {fi['unidad_pl']})": fmt(f["P50"].sum()),
                            f"Promedio por {fi['unidad']}": fmt(f["P50"].mean()),
                            f"Vs. últimos {H} {fi['unidad_pl']}": E.pct(v_e) if v_e is not None else "—",
                            "Rango probable (P10–P90)": f"{fmt(f['P10'].sum())} – {fmt(f['P90'].sum())}"})
        fig.add_vline(x=hist["fecha"].iloc[-1], line=dict(color=E.EJE, width=1, dash="dot"))
        fig.update_layout(title=f"{obj_v} · {nombre_vista}", yaxis_title=obj_v, height=460, showlegend=True)
        E.grafico(fig, key="fig_pron")
        supuestos = []
        if dp.tiene("precio"):
            supuestos.append("el precio se mantiene en su último valor")
        if dp.tiene("promocion"):
            supuestos.append("no hay promociones")
        st.caption("Línea tenue: historial. Línea gruesa, a la derecha de la línea punteada: pronóstico. La franja del "
                   "mismo color es el rango probable: en 8 de cada 10 períodos la realidad debería caer dentro."
                   + (" Supone que " + " y ".join(supuestos) + "." if supuestos else ""))
        st.dataframe(pd.DataFrame(filas_r), hide_index=True, width="stretch")

    elif vista == "Tabla":
        tabla = pd.concat([fut_v[e].assign(entidad=e) for e in sel])
        tabla[["P10", "P50", "P90"]] = tabla[["P10", "P50", "P90"]] * div
        tabla["fecha"] = tabla["fecha"].dt.strftime("%d/%m/%Y")
        tabla = tabla.rename(columns={"fecha": "Fecha", "entidad": nom_col, "P50": "Pronóstico",
                                      "P10": "Escenario bajo (P10)", "P90": "Escenario alto (P90)"})
        tabla = tabla[[nom_col, "Fecha", "Pronóstico", "Escenario bajo (P10)",
                                                         "Escenario alto (P90)"]]
        st.dataframe(tabla, width="stretch", hide_index=True, height=min(560, 38 + 35 * len(tabla)),
                     column_config={c: st.column_config.NumberColumn(format="%.0f") for c in tabla.columns[-3:]})

    else:
        st.caption(f"Medido pronosticando los últimos {res.plan.validacion} {fi['unidad_pl']} de tu historial como si "
                   "no los conociéramos, y comparando con lo que realmente pasó.")
        m = met.loc[[e for e in ver if e in met.index]]
        st.dataframe(pd.DataFrame({
            nom_col: m.index,
            "Error": m["wape"].map(lambda v: E.pct(v)),
            "Dentro del rango": m["cobertura"].map(lambda v: E.pct(v)),
            "Error sin modelo": m["wape_naive"].map(lambda v: E.pct(v)),
            "Mejora vs. sin modelo": ((m["wape_naive"] - m["wape"]) / m["wape_naive"].where(m["wape_naive"] > 0)
                                      * 100).map(lambda v: E.pct(v)),
        }), width="stretch", hide_index=True)

# ---------------------------------------------------------------- descarga
todo = pd.concat([f.assign(entidad=e) for e, f in fut.items()])
todo = todo[["entidad", "fecha", "P50", "P10", "P90"]].rename(columns={
    "entidad": S.mayus(S.nombre_entidad(dp)), "fecha": "Fecha", "P50": "Pronóstico",
    "P10": "Escenario bajo (P10)", "P90": "Escenario alto (P90)"}).round(1)
todo["Fecha"] = todo["Fecha"].dt.date
with st.container(horizontal=True, horizontal_alignment="right"):
    st.download_button("Descargar pronóstico (Excel)", E.excel_bytes({"Pronóstico": todo}),
                       file_name="pronostico.xlsx", icon=":material/download:", type="tertiary",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

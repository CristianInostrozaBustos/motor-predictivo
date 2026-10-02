"""Finanzas: lo que el pronóstico de demanda significa en plata (ingresos, margen, metas, capital e insumos)."""

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import estilo as E
import sesion as S
from motor import politica as P

dp, res = S.requiere_pronostico()
S.panel_dataset()
fi = dp.freq_info
H = S.horizonte()
fut = S.pronostico()
entidades = list(fut)
nom = S.mayus(S.nombre_entidad(dp))
dias_p = fi["dias"]

vista = S.vista("finanzas")
E.titulo_compacto("Paso 4 · Finanzas", vista)
val = S.valores(dp)


def precio_y_costo(ent):
    st.caption("Se toman de tu archivo cuando vienen. Puedes asignarlos o corregirlos aquí; se usan en todo el sitio. "
               "Sin precio, todo se muestra en unidades.")
    base = val.reset_index()[["entidad", "precio", "costo", "origen_precio", "origen_costo"]]
    base[["precio", "costo"]] = base[["precio", "costo"]].astype(float)
    base = base.rename(columns={
        "entidad": nom, "precio": "Precio de venta ($)", "costo": "Costo unitario ($)",
        "origen_precio": "Precio desde", "origen_costo": "Costo desde"})
    editado = st.data_editor(
        E.destacar_fila(base, nom, ent), hide_index=True, width="stretch", key=f"valores_{S.clave_dataset(dp)}",
        disabled=[nom, "Precio desde", "Costo desde"],
        column_config={
            "Precio de venta ($)": st.column_config.NumberColumn(min_value=0.0, step=1.0, format="%.0f"),
            "Costo unitario ($)": st.column_config.NumberColumn(min_value=0.0, step=1.0, format="%.0f",
                                                                help="Lo que te cuesta cada unidad."),
        },
    )
    nuevos = editado.set_index(nom).rename(columns={"Precio de venta ($)": "precio", "Costo unitario ($)": "costo"})
    antes = val[["precio", "costo"]].astype(float).fillna(-1).round(6)
    ahora = nuevos[["precio", "costo"]].astype(float).fillna(-1).round(6).reindex(antes.index)
    if not np.allclose(antes.to_numpy(), ahora.to_numpy()):
        S.guardar_valores(dp, nuevos[["precio", "costo"]])
        st.rerun()
    st.session_state["costo_mantener_pct"] = st.number_input(
        "Costo anual de mantener inventario (% de su valor)", 0.0, 100.0, S.costo_mantener_pct(), 1.0, format="%.0f",
        help="Bodega, capital inmovilizado, seguros y mermas. Un 20% al año es una referencia común.")
    raros = [e for e in val.index if pd.notna(val.loc[e, "precio"]) and pd.notna(val.loc[e, "costo"])
             and val.loc[e, "costo"] >= val.loc[e, "precio"]]
    if raros:
        st.caption(f":material/warning: En {', '.join(map(str, raros[:5]))} el costo es igual o mayor que el precio: "
                   "probablemente es el precio de un insumo. El margen de esos productos no se calcula.")


def comparar_productos(ent):
    filas = []
    for e in entidades:
        u = fut[e]["P50"].sum()
        p_e, c_e = S.precio(e), S.costo(e)
        fila = {nom: e, "Ventas (u.)": E.num(u), "_orden": u * (p_e or 0) if p_e else u}
        if S.hay_precios():
            fila["Ingresos"] = E.clp(u * p_e) if p_e else "—"
            fila["Margen"] = E.clp(u * (p_e - c_e)) if (p_e and c_e) else "—"
        filas.append(fila)
    tabla = pd.DataFrame(filas).sort_values("_orden", ascending=False).drop(columns="_orden")
    st.dataframe(E.destacar_fila(tabla, nom, ent), hide_index=True, width="stretch")


def insumos(ent):
    st.caption("Opcional. Indica cuánto insumo usa cada unidad vendida (por ejemplo, 18 g de café por taza) y el sitio "
               f"calcula cuánto necesita {ent} para el período: lo normal (pronóstico) y lo prudente (escenario alto).")
    clave_ins = f"insumos_{S.clave_dataset(dp)}"
    base_ins = st.session_state.get(clave_ins, pd.DataFrame({
        "Insumo": pd.Series(dtype="str"), "Cantidad por unidad": pd.Series(dtype="float"),
        "Unidad": pd.Series(dtype="str"), "Aplica a": pd.Series(dtype="str")}))
    ins = st.data_editor(base_ins, num_rows="dynamic", hide_index=True, width="stretch", key=f"ed_{clave_ins}",
                         column_config={
                             "Cantidad por unidad": st.column_config.NumberColumn(min_value=0.0, format="%.3f"),
                             "Aplica a": st.column_config.SelectboxColumn(options=["Todos"] + [str(e) for e in entidades]),
                         })
    st.session_state[clave_ins] = ins
    ins = ins.dropna(subset=["Insumo", "Cantidad por unidad"])
    if len(ins):
        filas = []
        for _, r in ins.iterrows():
            aplica_todos = pd.isna(r["Aplica a"]) or r["Aplica a"] in ("", "Todos")
            if not aplica_todos and str(r["Aplica a"]) != str(ent):
                continue
            normal = fut[ent]["P50"].sum() * r["Cantidad por unidad"]
            prudente = fut[ent]["P90"].sum() * r["Cantidad por unidad"]
            unidad = "" if pd.isna(r["Unidad"]) else f" {r['Unidad']}"
            filas.append({"Insumo": r["Insumo"], "Necesitas (normal)": f"{E.num(normal)}{unidad}",
                          "Prudente (escenario alto)": f"{E.num(prudente)}{unidad}"})
        if filas:
            st.markdown(f"#### Lo que necesita {ent} en los próximos {H} {fi['unidad_pl']}")
            st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch")
        else:
            st.caption(f":material/info: Ningún insumo de la tabla aplica a {ent}.")


# el producto elegido es el mismo en todas las vistas de Finanzas
ent = S.selector_entidad(entidades, dp, key="fin")
if vista == "Precio y costo":
    precio_y_costo(ent)
elif vista == "Comparar productos":
    comparar_productos(ent)
elif vista == "Insumos":
    insumos(ent)
else:
    ver = [ent]
    nombre_vista = str(ent)
    precios = {e: S.precio(e) for e in ver}
    costos = {e: S.costo(e) for e in ver}
    con_precio = [e for e in ver if precios[e]]
    en_pesos = bool(con_precio)

    def monto(e, serie):
        return serie * (precios[e] or 0.0)

    if vista == "Meta e ingresos":
        panel = st.container(border=True, key="panel_control_fin")
        tarjetas = st.container()
        unid = sum(fut[e]["P50"].sum() for e in ver)
        ingresos = {q: sum(monto(e, fut[e][q]).sum() for e in con_precio) for q in ("P10", "P50", "P90")}
        margen = sum((fut[e]["P50"].sum() * (precios[e] - costos[e])) for e in con_precio if costos[e])
        hay_margen = any(costos[e] for e in con_precio)
        dias_total = H * dias_p

        c1, c2, c3, c4 = tarjetas.columns(4)
        c1.metric(f"Ventas esperadas ({H} {fi['unidad_pl']})", f"{E.num(unid)} u.")
        if en_pesos:
            c2.metric("Ingresos esperados", E.clp_corto(ingresos["P50"]), help=f"{E.clp_md(ingresos['P50'])}")
        else:
            c2.metric("Ingresos esperados", "—", help="Asigna un precio de venta en Precio y costo para ver montos.")
        c3.metric("Margen esperado", E.clp_corto(margen) if hay_margen else "—",
                  help="Ingresos menos el costo de lo vendido. Requiere el costo unitario.")

        # capital en inventario (si hay inventario y costo)
        politica = st.session_state.get("politica")
        capital = None
        if politica and politica.get("clave") == S.clave_dataset(dp):
            tabla_pol = politica["tabla"].set_index("entidad")
            capital = 0.0
            for e in ver:
                cc = S.costo_compra(e)
                inv = tabla_pol["inventario"].get(e) if "inventario" in tabla_pol else None
                if cc and inv is not None and pd.notna(inv):
                    capital += float(inv) * cc
            capital = capital or None
        elif dp.tiene("inventario"):
            capital = 0.0
            for e in ver:
                cc = S.costo_compra(e)
                inv = dp.df.loc[dp.df["entidad"] == e, "inventario"].dropna()
                if cc and len(inv):
                    capital += float(inv.iloc[-1]) * cc
            capital = capital or None
        if capital:
            c4.metric("Capital en inventario hoy", E.clp_corto(capital),
                      delta=f"mantenerlo ≈ {E.clp_corto(capital * S.costo_mantener_pct() / 100 / 12).replace('$', chr(92) + '$')} al mes",
                      delta_color="off", delta_arrow="off",
                      help="Inventario actual × costo unitario. El costo de mantenerlo usa el % anual de Precio y costo.")
        else:
            c4.metric("Capital en inventario hoy", "—", help="Requiere inventario actual y costo unitario.")

        # error real del backtest, sumado por fecha (captura errores correlacionados)
        _err_total = None
        for e in ver:
            bt = res.backtest.get(e)
            if bt is None:
                continue
            factor = (precios[e] or 0.0) if en_pesos else 1.0
            serie = pd.Series(((bt["real"] - bt["P50"]) * factor).to_numpy(), index=pd.to_datetime(bt["fecha"]))
            _err_total = serie if _err_total is None else _err_total.add(serie, fill_value=0)

        def sigma_total(t):
            """Incertidumbre del total acumulado a t períodos (unidades o $), con el error real del modelo."""
            var_banda = 0.0
            for e in ver:
                f = fut[e].iloc[:t]
                banda = float(((f["P90"] - f["P10"]) / (2 * P.Z_P10_P90)).clip(lower=0).mean()) * math.sqrt(t)
                var_banda += (banda * ((precios[e] or 0.0) if en_pesos else 1.0)) ** 2
            real = P.sigma_acumulado(None if _err_total is None else _err_total.to_numpy(), t)
            return max(math.sqrt(var_banda), real)

        mu_total = ingresos["P50"] if en_pesos else unid
        fmt = E.clp if en_pesos else (lambda v: f"{E.num(v)} u.")
        fmt_md = E.clp_md if en_pesos else fmt
        sd_total = sigma_total(H)
        paso = 10 ** max(0, int(math.log10(max(mu_total, 1))) - 2)
        # por defecto, la meta es lo vendido en el mismo período del año pasado (respeta la estacionalidad);
        # si no hay un año de historia, lo vendido en el período anterior del mismo largo
        hist_v = dp.df[dp.df["entidad"].isin(ver)]
        f_ini = fut[ver[0]]["fecha"].iloc[0]
        f_fin = fut[ver[0]]["fecha"].iloc[-1]
        hace_un_ano = hist_v[(hist_v["fecha"] >= f_ini - pd.DateOffset(years=1)) & (hist_v["fecha"] <= f_fin - pd.DateOffset(years=1))]
        if hist_v["fecha"].min() <= f_ini - pd.DateOffset(years=1) and len(hace_un_ano):
            ultimos, ref_txt = hace_un_ano, f"en el mismo período del año pasado"
        else:
            ultimos = hist_v[hist_v["fecha"] > hist_v["fecha"].max() - pd.Timedelta(days=dias_total)]
            ref_txt = f"en los últimos {H} {fi['unidad_pl']}"
        anterior = float((ultimos["objetivo"] * ultimos["entidad"].map(precios).fillna(0)).sum() if en_pesos
                         else ultimos["objetivo"].sum())
        meta_def = float(round((anterior if anterior > 0 else mu_total) / paso) * paso)
        m1, m2 = panel.columns([1, 2], vertical_alignment="bottom")
        m2.caption(f"Por defecto: lo que vendiste {ref_txt} ({fmt_md(anterior)}).")
        if not en_pesos:
            m2.caption(f":material/info: {ent} no tiene precio, así que la meta va en unidades. Asígnalo en "
                       "**Precio y costo**.")
        meta = m1.number_input(f"Meta para los próximos {H} {fi['unidad_pl']} " + ("($)" if en_pesos else "(u.)"),
                               0.0, None, meta_def, float(paso), format="%.0f", key=f"meta_{S.clave_dataset(dp)}_{en_pesos}")
        prob = 0.5 * (1 - math.erf((meta - mu_total) / (sd_total * math.sqrt(2)))) if sd_total > 0 else float(mu_total >= meta)
        por_dia_meta, por_dia_pron = meta / H, mu_total / H
        color = "🟢" if prob >= 0.7 else ("🟠" if prob >= 0.4 else "🔴")
        bajo, alto = max(0.0, mu_total - P.Z_P10_P90 * sd_total), mu_total + P.Z_P10_P90 * sd_total
        E.nota(f"{color} <b>Probabilidad de cumplir la meta: {E.num(prob * 100)}%.</b> "
               f"Lo más probable es que el período cierre entre <b>{fmt(bajo)}</b> y <b>{fmt(alto)}</b>. "
               f"Necesitas {fmt(por_dia_meta)} por {fi['unidad']}; el pronóstico da {fmt(por_dia_pron)} por {fi['unidad']}"
               + (f" (te faltan {fmt(max(0, meta - mu_total))} en el período)." if meta > mu_total else
                  f" (lo superas por {fmt(mu_total - meta)})."))

        # gráfico acumulado: pronóstico con su rango contra la línea de la meta
        fechas = fut[ver[0]]["fecha"]
        acum = np.cumsum(sum(((fut[e]["P50"] * (precios[e] or 0.0)) if en_pesos else fut[e]["P50"]).to_numpy() for e in ver))
        puntos = sorted(set(np.linspace(1, H, min(H, 12)).astype(int)))
        sd_t = np.interp(np.arange(1, H + 1), puntos, [sigma_total(t) for t in puntos])
        div, eje_y, hov_y = E.escala_pesos(max(acum.max(), meta)) if en_pesos else (1.0, "Unidades", "%{y:,.0f} u.")
        acum, sd_t, meta_g = acum / div, sd_t / div, meta / div
        with st.container(border=True):
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=fechas, y=acum + P.Z_P10_P90 * sd_t, mode="lines", line=dict(width=0),
                                     hoverinfo="skip", showlegend=False))
            fig.add_trace(go.Scatter(x=fechas, y=np.clip(acum - P.Z_P10_P90 * sd_t, 0, None), mode="lines", line=dict(width=0),
                                     fill="tonexty", fillcolor=E.AZUL_BANDA, name="Rango probable", hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=fechas, y=acum, name="Pronóstico acumulado", line=dict(color=E.AZUL, width=2.4),
                                     hovertemplate=hov_y))
            fig.add_trace(go.Scatter(x=fechas, y=np.linspace(meta_g / H, meta_g, H), name="Meta", mode="lines",
                                     line=dict(color=E.NARANJO, width=1.8, dash="dash"), hovertemplate=hov_y))
            fig.update_layout(title=("Ingresos acumulados" if en_pesos else "Ventas acumuladas") + f" vs. tu meta · {nombre_vista}",
                              yaxis_title=eje_y, height=400)
            E.grafico(fig, key="fig_meta")
            st.caption("La probabilidad usa el error real que tuvo el modelo en la prueba con datos pasados, acumulado en el "
                       "período, no solo el rango diario.")
    else:
        hist = dp.df[dp.df["entidad"].isin(ver)].groupby("fecha")["objetivo"].sum()
        hist = hist[hist.index >= hist.index.max() - pd.Timedelta(days=730)]
        patron, etiquetas, que = None, None, None
        if dp.config.frecuencia == "D" and len(hist) >= 28:
            patron = hist.groupby(hist.index.dayofweek).mean()
            etiquetas = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
            que = "días"
        elif dp.config.frecuencia in ("W", "M") and hist.index.month.nunique() == 12:
            patron = hist.groupby(hist.index.month).mean()
            etiquetas = [m for m in E.MESES_ES]
            que = "meses"
        if patron is not None and patron.mean() > 0:
            rel = (patron / patron.mean() - 1) * 100
            mejor, peor = rel.idxmax(), rel.idxmin()
            nombre = (lambda i: etiquetas[i]) if que == "días" else (lambda i: etiquetas[i - 1])
            E.nota(f"Tu mejor {'día' if que == 'días' else 'mes'} es el <b>{nombre(mejor)}</b> "
                   f"({E.pct(rel[mejor])} sobre el promedio) y el más flojo el <b>{nombre(peor)}</b> ({E.pct(rel[peor])}).")
            with st.container(border=True):
                fig = go.Figure(go.Bar(x=[nombre(i).capitalize()[:3] for i in rel.index], y=rel.values,
                                       marker_color=[E.VERDE if v >= 0 else E.ROJO for v in rel.values],
                                       hovertemplate="%{y:+.1f}%<extra></extra>"))
                fig.update_layout(title=f"Ventas promedio por {'día de la semana' if que == 'días' else 'mes'} vs. el promedio",
                                  yaxis_ticksuffix="%", height=300, showlegend=False)
                E.grafico(fig, key="fig_patron")
        else:
            E.nota("Hace falta más historial para ver el patrón: al menos 4 semanas con datos diarios, o un año "
                   "completo con datos semanales o mensuales.")

# ---------------------------------------------------------------- descarga
hoja = []
for e in entidades:
    p_e = S.precio(e)
    f = fut[e][["fecha", "P50", "P10", "P90"]].copy()
    f.insert(0, nom, e)
    if p_e:
        for q in ("P50", "P10", "P90"):
            f[f"Ingresos {q}"] = f[q] * p_e
    f["fecha"] = f["fecha"].dt.date
    hoja.append(f.rename(columns={"fecha": "Fecha", "P50": "Ventas (u.)", "P10": "Ventas bajo (u.)",
                                  "P90": "Ventas alto (u.)"}))
with st.container(horizontal=True, horizontal_alignment="right"):
    st.download_button("Descargar finanzas (Excel)", E.excel_bytes({"Proyección": pd.concat(hoja).round(1)}),
                       file_name="finanzas.xlsx", icon=":material/download:", type="tertiary",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

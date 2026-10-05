import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import estilo as E
import sesion as S
from motor import datos as D
from motor import reglas as R

E.encabezado("Datos", "Información del dataset",
             "Cómo se leyeron tus datos, su resumen y cuánto quieres pronosticar.")

df = st.session_state.get("df_raw")
if df is None:
    S.panel_dataset()
    st.info("Primero sube o carga tus datos en **Inicio**.", icon=":material/upload_file:")
    st.page_link("paginas/inicio.py", label="Ir a Inicio", icon=":material/arrow_forward:")
    st.stop()

nombre = st.session_state["nombre_dataset"]
k = f"{nombre}_{len(df)}"
det = D.detectar_roles(df)

# ---------------------------------------------------------------- columnas (plegado)
NINGUNA = "(no tiene)"
columnas = list(df.columns)
problemas = not det.roles.get("fecha") or not det.roles.get("objetivo")
roles = {}
with st.expander("Revisar columnas detectadas", icon=":material/view_column:", expanded=problemas):
    for a in det.advertencias:
        st.warning(a, icon=":material/warning:")
    c1, c2, c3 = st.columns(3)
    for col, rol in zip((c1, c2, c3), ("fecha", "objetivo", "entidad")):
        opciones = ([NINGUNA] if rol == "entidad" else []) + columnas
        d = det.roles.get(rol)
        sel = col.selectbox(D.ROLES[rol]["etiqueta"], opciones,
                            index=opciones.index(d) if d in opciones else (0 if rol == "entidad" else None),
                            key=f"rol_{rol}_{k}", placeholder="Elige una columna")
        roles[rol] = None if sel == NINGUNA else sel
    nombre_serie = ""
    if roles.get("entidad"):
        c1, _ = st.columns([1, 2])
        nombre_serie = c1.text_input("¿Cómo se llama cada uno?", key=f"serie_{k}", max_chars=30,
                                     placeholder=S.nombre_sugerido(D.nombre_legible(roles["entidad"])),
                                     help="Se usa en los títulos y selectores del sitio, por ejemplo SKU, tienda, "
                                          "bebida o sucursal. Si lo dejas vacío se usa el nombre de la columna.")
    st.caption("Opcionales: si tu archivo las tiene, el sistema las usa para mejorar el pronóstico y las decisiones.")
    cols = st.columns(3)
    for i, rol in enumerate(["precio", "promocion", "lead_time", "inventario", "quiebre", "costo_unitario"]):
        opciones = [NINGUNA] + columnas
        d = det.roles.get(rol)
        sel = cols[i % 3].selectbox(D.ROLES[rol]["etiqueta"], opciones, index=opciones.index(d) if d in opciones else 0,
                                    key=f"rol_{rol}_{k}")
        roles[rol] = None if sel == NINGUNA else sel
    asignadas = {v for v in roles.values() if v}
    candidatas = [c for c in columnas if c not in asignadas and pd.api.types.is_numeric_dtype(df[c])
                  and not D.es_derivada_de_fecha(c)]
    exogenas = st.multiselect("Otras variables que influyen en la demanda", candidatas,
                              default=[c for c in det.exogenas if c in candidatas], key=f"exog_{k}",
                              format_func=D.nombre_legible,
                              placeholder="Ninguna",
                              help="Por ejemplo clima, tráfico o un índice de mercado.")

    st.caption("Opciones avanzadas")
    fechas_tmp, _ = D.parsear_fechas(df[roles["fecha"]]) if roles.get("fecha") else (None, None)
    freq_det, finfo = ("D", {"frac_duplicadas": 0})
    if fechas_tmp is not None and fechas_tmp.notna().mean() >= 0.5:
        freq_det, finfo = D.detectar_frecuencia(fechas_tmp, df[roles["entidad"]] if roles.get("entidad") else None)
    a1, a2, a3 = st.columns(3)
    codigos = list(D.FRECUENCIAS)
    frecuencia = a1.selectbox("Agrupar los datos por", codigos, index=codigos.index(freq_det), key=f"freq_{k}",
                              format_func=lambda c: D.FRECUENCIAS[c]["unidad"].capitalize())
    relleno = a2.selectbox("Períodos sin registro", ["interpolar", "cero"], key=f"relleno_{k}",
                           index=1 if finfo["frac_duplicadas"] > 0.2 else 0,
                           format_func=lambda x: {"interpolar": "Faltan datos (interpolar)", "cero": "No hubo ventas (cero)"}[x])
    negativos = a3.toggle("Tratar negativos como cero", value=True, key=f"neg_{k}")
    suavizar = S.SUAVIZAR_PICOS_DEFECTO

repetidas = [c for c in asignadas if list(roles.values()).count(c) > 1]
if repetidas:
    st.error(f"La columna '{repetidas[0]}' está asignada dos veces. Revisa las columnas.")
    st.stop()
if not roles.get("fecha") or not roles.get("objetivo"):
    st.error("Indica cuál columna tiene la fecha y cuál la cantidad que quieres pronosticar.")
    st.stop()
if fechas_tmp is None or fechas_tmp.notna().mean() < 0.5:
    st.error(f"La columna '{roles['fecha']}' no contiene fechas reconocibles.")
    st.stop()
if finfo.get("mediana_dias", 0) > 200:
    st.error("Tus datos parecen anuales (un registro por año). El sistema trabaja con datos diarios, semanales, "
             "mensuales o trimestrales.")
    st.stop()

try:
    dp = S.preparar_cacheado(df, tuple(sorted(roles.items())), tuple(exogenas), frecuencia, relleno, negativos,
                             suavizar)
except Exception as e:  # noqa: BLE001
    st.error(f"No pudimos preparar los datos: {e}")
    st.stop()
if dp.df["objetivo"].notna().sum() == 0:
    st.error(f"La columna '{roles['objetivo']}' no tiene valores numéricos. Elige la columna con las cantidades "
             "a pronosticar.")
    st.stop()
st.session_state["dp"] = dp
if st.session_state.get("_menu_dp") != S.clave_dataset(dp):     # el submenú lateral se arma antes que la página
    st.session_state["_menu_dp"] = S.clave_dataset(dp)
    st.rerun()
st.session_state["config_actual"] = dict(roles=roles, exogenas=list(exogenas), frecuencia=frecuencia, relleno=relleno,
                                         negativos=bool(negativos), nombre_serie=nombre_serie.strip(),
                                         suavizar_picos=bool(suavizar), inflacion=S.config_inflacion())
_lo = st.session_state.get("link_origen")
if _lo and _lo["nombre"] == nombre:
    st.session_state["config_actual"]["vivo"] = {**S.VIVO_DEFECTO, "link": _lo["url"], "link_base": True,
                                                 "hash": _lo["hash"]}
elif S.registro_actual() and S.registro_actual().get("vivo"):
    st.session_state["config_actual"]["vivo"] = S.registro_actual()["vivo"]
plan = R.planificar(dp)
fi = dp.freq_info
n_ent = dp.df["entidad"].nunique()

# ---------------------------------------------------------------- resumen amable
f0, f1 = dp.df["fecha"].min(), dp.df["fecha"].max()
datos_fr = {"diaria": "diarios", "semanal": "semanales", "mensual": "mensuales",
            "trimestral": "trimestrales"}.get(fi["nombre"], fi["nombre"])
st.markdown(
    f'<div class="resumen-chips"><span><b>{E.num(n_ent)}</b> {S.nombre_entidad(dp, n_ent != 1)}</span>'
    f'<span>Historial <b>{f0:%m/%Y} – {f1:%m/%Y}</b></span>'
    f'<span title="Frecuencia detectada en tu archivo">Datos <b>{datos_fr}</b></span></div>',
    unsafe_allow_html=True)

with st.container(border=True):
    todas = sorted(dp.df["entidad"].unique())
    sel = S.selector_vista(todas, dp, key="datos")
    fig = go.Figure()
    for e in sel:
        g = dp.df[dp.df["entidad"] == e]
        fig.add_trace(go.Scatter(x=g["fecha"], y=g["objetivo"], name=str(e), mode="lines",
                                 line=dict(color=E.color_sku(e, todas), width=1.4),
                                 hovertemplate="%{y:,.0f}"))
    pk = dp.picos[dp.picos["entidad"].isin(sel)]
    if len(pk):
        fig.add_trace(go.Scatter(x=pk["fecha"], y=pk["valor"], mode="markers", name="Pico aislado",
                                 marker=dict(color=E.ROJO, size=9, symbol="circle-open", line=dict(width=2)),
                                 customdata=pk["tipico"], hovertemplate="%{y:,.0f} (lo típico: %{customdata:,.0f})"))
    fig.update_layout(title=f"{dp.etiquetas['objetivo']} por {fi['unidad']} · {S.titulo_seleccion(sel, dp)}",
                      height=380, showlegend=True)
    E.grafico(fig, key="fig_total")
    if len(dp.picos):
        n_e = dp.picos["entidad"].nunique()
        st.caption(f":material/troubleshoot: Se detectaron {len(dp.picos)} picos aislados en {n_e} "
                   f"{S.nombre_entidad(dp, n_e != 1)}: ventas muy por encima de lo normal que no se repiten. "
                   + "El modelo aprende sin ellos; tu historial y la medición del error no cambian.")
        with st.expander("Ver picos detectados"):
            st.dataframe(pd.DataFrame({
                S.mayus(S.nombre_entidad(dp)): dp.picos["entidad"],
                "Fecha": pd.to_datetime(dp.picos["fecha"]).dt.strftime("%d/%m/%Y"),
                "Valor": dp.picos["valor"].map(E.num),
                "Lo típico en esa fecha": dp.picos["tipico"].map(E.num),
                "Veces lo típico": (dp.picos["valor"] / dp.picos["tipico"]).map(lambda v: E.num(v, 1) + "×"),
            }), hide_index=True, width="stretch")

if not plan.viable:
    st.error(f"El historial es demasiado corto para pronosticar: se necesitan al menos "
             f"{R.minimo_registros(dp.config.frecuencia)} {fi['unidad_pl']} por {S.nombre_entidad(dp)}.")
    S.panel_dataset()
    st.stop()
if plan.entidades_excluidas:
    st.warning(f"{len(plan.entidades_excluidas)} {S.nombre_entidad(dp, True)} tienen muy poco historial y no se "
               f"pronosticarán: {', '.join(map(str, plan.entidades_excluidas[:8]))}"
               f"{'…' if len(plan.entidades_excluidas) > 8 else ''}.", icon=":material/warning:")

# ---------------------------------------------------------------- siguiente paso
_, res = S.resultado()
if res is not None:
    S.actualizar_registro(config=st.session_state["config_actual"])
st.page_link("paginas/pronostico.py", label="Ver pronóstico" if res is not None else "Siguiente: generar el pronóstico",
             icon=":material/arrow_forward:")

S.panel_dataset()

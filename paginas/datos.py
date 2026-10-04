import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import estilo as E
import sesion as S
from motor import datos as D
from motor import reglas as R

E.encabezado(
    "Tus datos",
    "Sube tu historial",
    "Ventas o demanda en CSV o Excel. El sistema reconoce las columnas y deja todo listo para pronosticar.",
)

# ---------------------------------------------------------------- análisis anteriores (con sesión iniciada)
import cuenta  # noqa: E402

_u = cuenta.usuario()
if _u:
    try:
        _anteriores = S.repositorio().listar(_u["correo"])
    except Exception:  # noqa: BLE001
        _anteriores = []
    if _anteriores:
        with st.container(border=True):
            c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
            _reg = c1.selectbox(
                "Cargar un análisis anterior", _anteriores, index=None, placeholder="Elige uno de tus análisis",
                format_func=lambda r: (f"{r['nombre']} · {r.get('n_entidades') or 0} "
                                       f"{'serie' if (r.get('n_entidades') or 0) == 1 else 'series'} · "
                                       f"{D.FRECUENCIAS.get(r.get('frecuencia') or 'D', D.FRECUENCIAS['D'])['nombre']} · "
                                       f"{str(r.get('actualizado') or '')[:10]}"),
                key="cargar_anterior", help="Recupera el archivo, la configuración y el modelo ya entrenado.")
            if c2.button("Abrir", type="primary", width="stretch", disabled=_reg is None, key="abrir_anterior"):
                barra = st.progress(0.0, text="Abriendo…")
                try:
                    S.abrir_pronostico(_reg, lambda f, t: barra.progress(min(f, 1.0), text=t))
                    barra.empty()
                    st.switch_page("paginas/pronostico.py")
                except Exception as e:  # noqa: BLE001
                    barra.empty()
                    st.error(f"No se pudo abrir: {e}")
elif cuenta.login_disponible():
    st.caption(":material/history: Inicia sesión con Google (barra lateral) para volver a tus análisis anteriores "
               "sin subir el archivo de nuevo.")

# ---------------------------------------------------------------- origen
with st.container(border=True):
    origen = st.segmented_control("Origen", ["Subir archivo", "Usar un ejemplo"], default="Subir archivo",
                                  label_visibility="collapsed", key="origen_datos")
    if origen == "Usar un ejemplo":
        opciones = list(S.EJEMPLOS)
        elegido = st.radio("Dataset de ejemplo", opciones, format_func=lambda a: S.EJEMPLOS[a][0],
                           captions=[S.EJEMPLOS[a][1] for a in opciones], label_visibility="collapsed")
        st.caption(":material/info: Datos de ejemplo generados para probar el sistema.")
        if st.button("Cargar ejemplo", type="primary", icon=":material/download:"):
            st.session_state["df_raw"] = S.cargar_ejemplo(elegido)
            st.session_state["nombre_dataset"] = elegido
    else:
        archivo = st.file_uploader("Archivo CSV o Excel", type=["csv", "xlsx", "xls"], label_visibility="collapsed")
        if archivo is not None and archivo.name != st.session_state.get("nombre_dataset"):
            try:
                st.session_state["df_raw"] = S.leer(archivo.name, archivo.getvalue())
                st.session_state["archivo_bytes"] = archivo.getvalue()
                st.session_state["nombre_dataset"] = archivo.name
            except Exception as e:  # noqa: BLE001
                st.error(f"No pudimos leer el archivo: {e}")

df = st.session_state.get("df_raw")
if df is None:
    S.panel_dataset()
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
st.session_state["config_actual"] = dict(roles=roles, exogenas=list(exogenas), frecuencia=frecuencia, relleno=relleno,
                                         negativos=bool(negativos), nombre_serie=nombre_serie.strip(),
                                         suavizar_picos=bool(suavizar))
plan = R.planificar(dp)
fi = dp.freq_info
n_ent = dp.df["entidad"].nunique()

# ---------------------------------------------------------------- resumen amable
f0, f1 = dp.df["fecha"].min(), dp.df["fecha"].max()
c1, c2, c3 = st.columns(3)
c1.metric(S.mayus(S.nombre_entidad(dp, n_ent != 1)), E.num(n_ent))
c2.metric("Historial", f"{f0:%m/%Y} – {f1:%m/%Y}")
c3.metric("Datos", fi["nombre"].capitalize(), help="Frecuencia detectada en tu archivo.")

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

# ---------------------------------------------------------------- horizonte y botón
st.markdown("## ¿Cuánto quieres pronosticar?")
clave = S.clave_dataset(dp)
_, res = S.resultado()
with st.container(border=True):
    h_prev = st.session_state.get("horizonte") or plan.horizonte_defecto
    h = st.slider(f"{fi['unidad_pl'].capitalize()} hacia adelante", 1, plan.horizonte_max,
                  min(h_prev, plan.horizonte_max), key=f"h_{clave}")
    st.caption(f"Hasta {plan.horizonte_max} {fi['unidad_pl']} según el historial disponible. "
               f"El pronóstico empieza el {(f1 + pd.tseries.frequencies.to_offset(D.FRECUENCIAS[dp.config.frecuencia]['pandas'])):%d/%m/%Y}.")
    if res is None:
        generar = st.button("Generar pronóstico", type="primary", icon=":material/auto_graph:")
        if generar:
            barra = st.progress(0.0, text="Preparando…")
            res_nuevo, origen = S.entrenar(clave, dp, plan, lambda frac, txt: barra.progress(min(frac, 1.0), text=txt))
            barra.empty()
            st.session_state["resultado"] = {"clave": clave, "res": res_nuevo, "origen": origen}
            st.session_state["horizonte"] = h
            st.rerun()
        if S.modelo_guardado(clave):
            st.caption(":material/bolt: Estos datos ya se analizaron antes: el pronóstico sale al instante.")
        else:
            st.caption(":material/schedule: Toma entre 1 y 4 minutos según el tamaño de tus datos. El sistema elige solo "
                       "la mejor forma de entrenar para tu dataset, y la guarda para la próxima vez.")
    else:
        st.session_state["horizonte"] = h
        S.actualizar_registro(horizonte=int(h))
        S.actualizar_registro(config=st.session_state["config_actual"])
        origen = st.session_state["resultado"].get("origen")
        import cuenta
        error_guardado = S.guardar_pronostico_actual()
        if error_guardado:
            st.session_state.setdefault("avisos_almacen", []).append(error_guardado)
        st.success("Pronóstico listo." + (" Recuperado de un análisis anterior de estos mismos datos." if origen == "guardado" else ""),
                   icon=":material/check_circle:")
        reg = S.registro_actual()
        if reg:
            st.caption(f":material/cloud_done: Guardado en **Mis pronósticos** como “{reg['nombre']}”.")
        elif cuenta.login_disponible() and not cuenta.usuario():
            st.caption(":material/lock_open: Estás usando el modo abierto: el pronóstico no se guarda a tu nombre. "
                       "Inicia sesión (barra lateral) para guardarlo y volver a él cuando quieras.")
        for aviso in st.session_state.pop("avisos_almacen", []):
            if S.modo_dev():
                st.warning(aviso, icon=":material/cloud_off:")
        b1, b2, b3 = st.columns(3)
        b1.page_link("paginas/pronostico.py", label="Ver pronóstico", icon=":material/show_chart:")
        b2.page_link("paginas/decisiones.py", label="Ver decisiones de abastecimiento", icon=":material/inventory_2:")
        b3.page_link("paginas/analisis.py", label="Explorar mis datos", icon=":material/insights:")

S.panel_dataset()

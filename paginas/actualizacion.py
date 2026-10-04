from datetime import date, datetime

import pandas as pd
import streamlit as st

import cuenta
import estilo as E
import sesion as S
from motor import vivo as V

S.panel_dataset()
E.encabezado("Tus datos", "Actualización en tiempo real",
             "Cuando llegan ventas o inventario nuevos, tu pronóstico se pone al día.")

u = cuenta.usuario()
if not u:
    st.info("Inicia sesión con Google (barra lateral) para conectar tus datos y que el pronóstico se actualice solo.",
            icon=":material/login:")
    st.stop()
r = S.registro_actual()
if not r:
    st.info("Primero genera un pronóstico en **Cargar datos**, o abre uno de tus análisis anteriores.",
            icon=":material/info:")
    st.page_link("paginas/datos.py", label="Ir a Cargar datos", icon=":material/arrow_forward:")
    st.stop()

dp = st.session_state["dp"]
cfg = st.session_state.get("config_actual") or {}
roles = cfg.get("roles") or {}
con_ent = bool(roles.get("entidad"))
nom_ent = S.nombre_entidad(dp)
vivo = S.config_vivo(cfg)
almacen = S.almacen_vivo()


def fecha_corta(valor):
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(valor)[:16]


# ---------------------------------------------------------------- estado
est = S.estado_vivo()
with st.container(border=True):
    c1, c2 = st.columns([3, 1.2], vertical_alignment="center")
    if est["error_link"]:
        c1.markdown(f":material/link_off: **No pudimos leer la planilla conectada.** {est['error_link']}")
    elif est["error"]:
        c1.markdown(f":material/error: {est['error']}")
    elif S.hay_pendientes(est):
        c1.markdown(f'<span class="globo-rojo">1</span> **Hay datos nuevos**: {S.texto_pendientes(est, dp)}',
                    unsafe_allow_html=True)
        if c2.button("Actualizar ahora", type="primary", icon=":material/refresh:", width="stretch"):
            err = S.actualizar_ahora()
            st.error(err) if err else st.rerun()
    else:
        ultima = f" Última actualización: {fecha_corta(vivo['ultima'])}." if vivo.get("ultima") else ""
        c1.markdown(f":material/check_circle: **Todo al día.** Tu pronóstico ya usa los últimos datos.{ultima}")
    if not S.hay_pendientes(est) and c2.button("Revisar ahora", icon=":material/sync:", width="stretch"):
        S.estado_vivo(refrescar=True)
        st.rerun()

# ---------------------------------------------------------------- cuándo actualizar
with st.container(border=True):
    st.markdown("**Cuándo actualizar**")
    auto = st.toggle("Actualizar automáticamente", value=vivo["modo"] == "auto",
                     help="Apenas llegan datos nuevos, el sistema vuelve a entrenar con ellos.")
    cada = vivo.get("cada", "pedido")
    if not auto:
        cada = st.radio("Cuándo quieres actualizar", list(S.CADA), index=list(S.CADA).index(cada)
                        if cada in S.CADA else 0, format_func=S.CADA.get, horizontal=True)
        st.caption("Mientras tanto, cuando haya datos nuevos verás el aviso rojo en **1. Tus datos**.")
    else:
        st.caption("El reentrenamiento toma entre 1 y 4 minutos: no cierres la pestaña mientras dice "
                   "*Actualizando tu pronóstico*.")
    nuevo = {"modo": "auto" if auto else "manual", "cada": cada}
    if nuevo != {"modo": vivo["modo"], "cada": vivo.get("cada", "pedido")}:
        S.guardar_config_vivo(nuevo)
        st.rerun()

# ---------------------------------------------------------------- planilla conectada
with st.container(border=True):
    st.markdown("**Planilla conectada**")
    if vivo.get("link"):
        st.markdown(f":material/table: [{'Google Sheets' if 'docs.google.com' in vivo['link'] else 'Planilla'}]"
                    f"({vivo['link']})" + (" · es el origen de tus datos" if vivo.get("link_base") else
                                           " · agrega filas a tu archivo"))
        st.caption("Agrega o corrige filas en la planilla: el sistema la revisa cada vez que abres el sitio.")
        if st.button("Desconectar planilla", icon=":material/link_off:"):
            S.guardar_config_vivo({"link": None, "link_base": None, "hash": None})
            st.rerun()
    else:
        st.caption("Conecta una planilla de Google (compartida como \"Cualquier persona con el enlace\") o un "
                   "CSV/Excel público con las mismas columnas de tu archivo. Las filas nuevas se suman a tus datos.")
        c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
        url = c1.text_input("Link", placeholder="https://docs.google.com/spreadsheets/d/…", key="link_conectar")
        if c2.button("Conectar", width="stretch", disabled=not url.strip()):
            try:
                n = len(S.filas_del_link(url.strip(), roles))
                S.guardar_config_vivo({"link": url.strip(), "link_base": False})
                st.session_state["vivo_msg"] = f"Planilla conectada: leímos {E.num(n)} filas."
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(str(e) if isinstance(e, ValueError) else f"No se pudo leer el link ({type(e).__name__}).")

if st.session_state.get("vivo_msg"):
    st.success(st.session_state.pop("vivo_msg"), icon=":material/check_circle:")

# ---------------------------------------------------------------- a mano
with st.expander("Agregar datos a mano", icon=":material/edit:"):
    st.caption("Para anotar unas pocas filas: las ventas de los últimos días o el inventario de hoy.")
    columnas = {"fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY", default=date.today())}
    if con_ent:
        columnas["entidad"] = st.column_config.SelectboxColumn(S.mayus(nom_ent), options=dp.entidades)
    columnas.update(ventas=st.column_config.NumberColumn("Ventas", format="%.2f"),
                    inventario=st.column_config.NumberColumn("Inventario", min_value=0, format="%.0f"),
                    precio=st.column_config.NumberColumn("Precio", min_value=0, format="%.2f"))
    vacia = pd.DataFrame({c: pd.Series(dtype="datetime64[ns]" if c == "fecha" else (object if c == "entidad"
                                                                                    else float)) for c in columnas})
    tabla = st.data_editor(vacia, column_config=columnas, num_rows="dynamic", hide_index=True,
                           key=f"form_{r['id']}", width="stretch")
    if st.button("Guardar filas", icon=":material/save:", type="primary"):
        filas = V.filas_de_editor(tabla)
        if not filas:
            st.warning("Completa al menos una fila con ventas, inventario o precio.")
        else:
            try:
                limpias = V.validar_filas(filas, con_ent, roles)
                almacen.guardar_filas(u["correo"], r["id"], limpias, origen="formulario")
                st.session_state.pop(f"form_{r['id']}", None)
                st.session_state.pop("vivo_estado", None)
                st.session_state["vivo_msg"] = f"Guardamos {E.num(len(limpias))} fila{'s' if len(limpias) != 1 else ''}."
                st.rerun()
            except V.FilasInvalidas as e:
                st.error("Revisa estas filas: " + "; ".join(
                    f"fila {x['fila'] + 1}: {x['motivo']}" if x["fila"] is not None else x["motivo"]
                    for x in e.errores[:5]))

# ---------------------------------------------------------------- conexión por API (solo desarrollador)
if S.modo_dev():
    with st.expander("Conexión por API (desarrollador)", icon=":material/key:"):
        try:
            k = almacen.claves(u["correo"]).get(r["id"])
            recibidas = almacen.leer_filas(u["correo"], r["id"])
        except Exception as e:  # noqa: BLE001
            st.error(f"No se pudo leer la API: {type(e).__name__}. ¿Corriste el SQL de datos en vivo en Supabase?")
            st.stop()
        nueva = st.session_state.get("clave_nueva", {}).get(r["id"])
        if nueva:
            st.success("Copia la clave ahora: no se vuelve a mostrar.", icon=":material/key:")
            st.code(nueva, language=None)
        elif k:
            uso = fecha_corta(k["ultimo_uso"]) if k.get("ultimo_uso") else "sin uso"
            st.markdown(f"Clave activa `{k['prefijo']}…` · creada {fecha_corta(k['creado'])} · último envío: {uso}")
        c1, c2 = st.columns(2)
        if c1.button("Cambiar clave" if k else "Crear clave", icon=":material/key:", width="stretch"):
            st.session_state.setdefault("clave_nueva", {})[r["id"]] = almacen.crear_clave(u["correo"], r["id"])
            st.rerun()
        if k and c2.button("Desactivar clave", icon=":material/block:", width="stretch"):
            almacen.desactivar_clave(u["correo"], r["id"])
            st.session_state.get("clave_nueva", {}).pop(r["id"], None)
            st.rerun()
        api = S.url_api()
        if api:
            ent_json = f'"entidad": "{dp.entidades[0]}", ' if con_ent else ""
            st.code(f"""curl -X POST {api}/v1/datos \\\\
  -H "X-Clave: TU_CLAVE" -H "Content-Type: application/json" \\\\
  -d '{{"filas": [{{"fecha": "{date.today().isoformat()}", {ent_json}"ventas": 120, "inventario": 850}}]}}'""",
                    language="bash")
            st.caption(f"Documentación: {api}/docs")
        else:
            st.caption("Falta `[api] url` en los secrets.")
        if recibidas:
            n = len(recibidas)
            st.caption(f"Recibida{'s' if n != 1 else ''} {E.num(n)} fila{'s' if n != 1 else ''} por API o a mano.")
            t = pd.DataFrame(recibidas).sort_values("recibido").tail(50).iloc[::-1]
            ver = {"fecha": "Fecha", "entidad": S.mayus(nom_ent), "objetivo": "Ventas", "inventario": "Inventario",
                   "precio": "Precio", "origen": "Origen"}
            if not con_ent:
                ver.pop("entidad")
            st.dataframe(t[list(ver)].rename(columns=ver), hide_index=True, width="stretch")

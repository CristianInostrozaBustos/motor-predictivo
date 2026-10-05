from datetime import date, datetime

import pandas as pd
import streamlit as st

import cuenta
import estilo as E
import sesion as S

S.panel_dataset()
E.encabezado("Datos", "Actualización en tiempo real",
             "Cuando llegan ventas o inventario nuevos, tu pronóstico se pone al día.")

u = cuenta.usuario()
if not u:
    st.info("Inicia sesión con Google (barra lateral) para conectar tus datos y que el pronóstico se actualice solo.",
            icon=":material/login:")
    st.stop()
r = S.registro_actual()
if not r:
    st.info("Primero carga tus datos en **Inicio** y genera el pronóstico, o abre uno de tus análisis anteriores.",
            icon=":material/info:")
    st.page_link("paginas/inicio.py", label="Ir a Inicio", icon=":material/arrow_forward:")
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
en_curso = S.trabajo_activo() is not None
with st.container(border=True):
    c1, c2 = st.columns([3, 1.2], vertical_alignment="center")
    if est["error_link"]:
        c1.markdown(f":material/link_off: **No pudimos leer la planilla conectada.** {est['error_link']}")
    elif est["error"]:
        c1.markdown(f":material/error: {est['error']}")
    elif en_curso:
        c1.markdown(":material/cloud_sync: Incorporando los datos nuevos a tu pronóstico.")
    elif S.hay_pendientes(est):
        c1.markdown(f'<span class="globo-rojo">1</span> **Hay datos nuevos**: {S.texto_pendientes(est, dp)}',
                    unsafe_allow_html=True)
        if c2.button("Actualizar ahora", type="primary", icon=":material/refresh:", width="stretch"):
            err = S.actualizar_ahora()
            if err:
                st.error(err)
            else:
                st.rerun()
    else:
        ultima = f" Última actualización: {fecha_corta(vivo['ultima'])}." if vivo.get("ultima") else ""
        c1.markdown(f":material/check_circle: **Todo al día.** Tu pronóstico ya usa los últimos datos.{ultima}")
    if not S.hay_pendientes(est) and not en_curso and c2.button("Revisar ahora", icon=":material/sync:", width="stretch"):
        S.estado_vivo(refrescar=True)
        st.rerun()

if st.session_state.get("_auto_error"):
    st.error(f"La actualización automática no pudo partir. {st.session_state['_auto_error']}", icon=":material/error:")

# ---------------------------------------------------------------- cuándo actualizar
with st.container(border=True):
    st.markdown("**Cuándo actualizar**")
    actual = "auto" if vivo["modo"] == "auto" else ("semana" if vivo.get("cada") in ("semana", "dia") else "pedido")
    eleccion = st.radio("Cuándo actualizar", list(S.MODOS), index=list(S.MODOS).index(actual),
                        format_func=S.MODOS.get, label_visibility="collapsed")
    if eleccion != actual:
        if eleccion == "auto":
            st.session_state.pop("_auto_hecho", None)
            st.session_state.pop("_auto_fallo", None)
        elif actual == "auto":
            S.detener_automatico()
        S.guardar_config_vivo({"modo": "auto" if eleccion == "auto" else "manual",
                               "cada": "semana" if eleccion == "semana" else "pedido"})
        st.rerun()

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

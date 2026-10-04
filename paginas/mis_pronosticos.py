from datetime import date, datetime

import pandas as pd
import streamlit as st

import cuenta
import estilo as E
import sesion as S
from motor import almacen as A
from motor import repositorio as Rp
from motor.datos import FRECUENCIAS

S.panel_dataset()
E.encabezado("Tu cuenta", "Mis pronósticos",
             "Tus pronósticos guardados, con sus datos, política y escenarios. Ábrelos sin volver a subir el archivo.")

u = cuenta.usuario()
if not u:
    if cuenta.login_disponible():
        with st.container(border=True):
            st.markdown("**Inicia sesión para guardar tus pronósticos**")
            st.caption("Sin sesión puedes usar todo el sitio, pero nada queda guardado a tu nombre. Con sesión, cada "
                       "pronóstico que generes queda aquí junto con tu archivo, la política y los escenarios.")
            if st.button("Continuar con Google" if cuenta.login_google_disponible() else "Iniciar sesión (simulado)",
                         icon=":material/login:", type="primary", key="login_mis"):
                cuenta.iniciar_sesion()
    else:
        st.info("El inicio de sesión todavía no está configurado en este sitio.", icon=":material/info:")
    st.stop()

repo = S.repositorio()
try:
    lista = repo.listar(u["correo"])
except Exception as e:  # noqa: BLE001
    st.error(f"No pudimos leer tus pronósticos: {e}")
    st.stop()

# pronóstico abierto que todavía no está guardado (por ejemplo, se generó antes de iniciar sesión)
dp_act, res_act = S.resultado()
if res_act is not None and not S.registro_actual():
    with st.container(border=True):
        c1, c2 = st.columns([3, 1], vertical_alignment="center")
        c1.markdown(f"**Tienes un pronóstico abierto sin guardar** · {st.session_state.get('nombre_dataset', '')}")
        if c2.button("Guardarlo", icon=":material/save:", width="stretch"):
            err = S.guardar_pronostico_actual()
            st.error(err) if err else st.rerun()

if not lista:
    st.info("Todavía no tienes pronósticos guardados. Genera uno en **1. Tus datos** y quedará aquí automáticamente.",
            icon=":material/inbox:")
    st.page_link("paginas/datos.py", label="Ir a Tus datos", icon=":material/arrow_forward:")
    st.stop()


def fecha_corta(valor):
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(valor)[:16]


activo = (S.registro_actual() or {}).get("id")
try:
    claves_vivo = S.almacen_vivo().claves(u["correo"])
except Exception:  # noqa: BLE001
    claves_vivo = {}


def _guardar_link(reg, url):
    cfg = dict(reg.get("config") or {})
    if url:
        cfg["vivo_link"] = url
    else:
        cfg.pop("vivo_link", None)
    repo.actualizar(u["correo"], reg["id"], {"config": cfg})
    if reg["id"] == activo:
        st.session_state["config_actual"] = {**(st.session_state.get("config_actual") or {}), **cfg}
        if not url:
            st.session_state["config_actual"].pop("vivo_link", None)
        st.session_state["registro"]["vivo_link"] = url or None
        st.session_state.pop("vivo_estado", None)


def panel_vivo(reg):
    """Clave de integración, link y formulario para alimentar el pronóstico con datos nuevos."""
    vivo = S.almacen_vivo()
    cfg = reg.get("config") or {}
    roles = cfg.get("roles") or {}
    con_ent = bool(roles.get("entidad"))
    nom_ent = (cfg.get("nombre_serie") or (S.nombre_sugerido(roles["entidad"]) if roles.get("entidad") else "")
               or "serie").strip()
    try:
        r = vivo.resumen(u["correo"], reg["id"])
        if r["n"]:
            st.caption(f":material/sensors: Recibidas {E.num(r['n'])} filas ({E.num(r['n_ventas'])} con ventas) · "
                       f"última fecha {pd.Timestamp(r['ultima_fecha']).strftime('%d/%m/%Y')}")
    except Exception as e:  # noqa: BLE001
        st.error(f"No se pudieron leer los datos en vivo: {type(e).__name__}. Revisa que corriste el SQL nuevo en "
                 "Supabase (tablas integraciones y datos_vivo).")
        return
    if reg["id"] == activo:
        S.aviso_datos_nuevos(clave_boton=f"vivo_act_{reg['id']}")
    elif r["n_ventas"]:
        st.caption("Abre este pronóstico para incorporar los datos nuevos (te aparecerá el botón "
                   "**Actualizar pronóstico**).")

    t1, t2, t3 = st.tabs(["Clave de integración", "Link a una planilla", "Formulario"])
    with t1:
        st.caption("Para que tu sistema (ERP, punto de venta o una planilla con script) envíe ventas e inventario "
                   "solo, sin que tengas que subir archivos.")
        k = claves_vivo.get(reg["id"])
        nueva = st.session_state.get("clave_nueva", {}).get(reg["id"])
        if nueva:
            st.success("Copia tu clave ahora: por seguridad no la volveremos a mostrar.", icon=":material/key:")
            st.code(nueva, language=None)
        elif k:
            uso = fecha_corta(k["ultimo_uso"]) if k.get("ultimo_uso") else "todavía sin uso"
            st.markdown(f"Clave activa `{k['prefijo']}…` · creada {fecha_corta(k['creado'])} · último envío: {uso}")
        c1, c2 = st.columns(2)
        if c1.button("Cambiar clave" if k else "Crear clave", key=f"kc_{reg['id']}", icon=":material/key:",
                     width="stretch", help="La clave anterior deja de funcionar." if k else None):
            st.session_state.setdefault("clave_nueva", {})[reg["id"]] = vivo.crear_clave(u["correo"], reg["id"])
            st.rerun()
        if k and c2.button("Desactivar", key=f"kd_{reg['id']}", icon=":material/block:", width="stretch"):
            vivo.desactivar_clave(u["correo"], reg["id"])
            st.session_state.get("clave_nueva", {}).pop(reg["id"], None)
            st.rerun()
        api = S.url_api()
        if api:
            ent_json = f'"entidad": "{nom_ent.upper()}-001", ' if con_ent else ""
            st.markdown("**Cómo enviar datos**")
            st.code(f"""curl -X POST {api}/v1/datos \\
  -H "Authorization: Bearer TU_CLAVE" \\
  -H "Content-Type: application/json" \\
  -d '{{"filas": [{{"fecha": "2026-10-04", {ent_json}"ventas": 120, "inventario": 850}}]}}'""", language="bash")
            st.caption(f"Cada fila: **fecha** (AAAA-MM-DD){f', **entidad** ({nom_ent})' if con_ent else ''} y al menos "
                       f"uno de **ventas**, **inventario** o **precio**. Hasta 5.000 filas por envío. Si repites una "
                       f"fecha{' y ' + nom_ent if con_ent else ''}, se reemplaza. Documentación: {api}/docs")
        else:
            st.caption(":material/info: El servicio que recibe los datos todavía no está publicado en este sitio.")
    with t2:
        st.caption("Pega el link de una planilla de Google (compartida como \"Cualquier persona con el enlace\") o de "
                   "un CSV/Excel público. Se lee cada vez que abres el pronóstico. Puede tener las mismas columnas que "
                   "tu archivo o: fecha" + (f", {nom_ent}" if con_ent else "") + ", ventas, inventario.")
        actual = cfg.get("vivo_link") or ""
        url = st.text_input("Link", actual, key=f"link_{reg['id']}", placeholder="https://docs.google.com/spreadsheets/d/…")
        c1, c2 = st.columns(2)
        if c1.button("Guardar link", key=f"lg_{reg['id']}", icon=":material/link:", width="stretch",
                     disabled=not url.strip()):
            try:
                filas = S.filas_del_link(url.strip(), roles)
                _guardar_link(reg, url.strip())
                st.success(f"Listo: leímos {E.num(len(filas))} filas del link.", icon=":material/check_circle:")
            except Exception as e:  # noqa: BLE001
                st.error(str(e) if isinstance(e, ValueError) else f"No se pudo leer el link ({type(e).__name__}).")
        if actual and c2.button("Quitar link", key=f"lq_{reg['id']}", icon=":material/link_off:", width="stretch"):
            _guardar_link(reg, None)
            st.rerun()
    with t3:
        st.caption("Para cargar a mano unas pocas filas: ventas de los últimos días o el inventario de hoy.")
        dp_act = st.session_state.get("dp") if reg["id"] == activo else None
        columnas = {"fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY", default=date.today())}
        if con_ent:
            columnas["entidad"] = (st.column_config.SelectboxColumn(S.mayus(nom_ent), options=dp_act.entidades)
                                   if dp_act is not None else st.column_config.TextColumn(S.mayus(nom_ent)))
        columnas.update(ventas=st.column_config.NumberColumn("Ventas", format="%.2f"),
                        inventario=st.column_config.NumberColumn("Inventario", min_value=0, format="%.0f"),
                        precio=st.column_config.NumberColumn("Precio", min_value=0, format="%.2f"))
        vacia = pd.DataFrame({c: pd.Series(dtype="datetime64[ns]" if c == "fecha" else
                                           (object if c == "entidad" else float)) for c in columnas})
        tabla = st.data_editor(vacia, column_config=columnas, num_rows="dynamic", hide_index=True,
                               key=f"form_{reg['id']}", width="stretch")
        if st.button("Guardar filas", key=f"fg_{reg['id']}", icon=":material/save:", type="primary"):
            from motor import vivo as V
            filas = V.filas_de_editor(tabla)
            if not filas:
                st.warning("Completa al menos una fila con ventas, inventario o precio.")
            else:
                try:
                    limpias = V.validar_filas(filas, con_ent, roles)
                    vivo.guardar_filas(u["correo"], reg["id"], limpias, origen="formulario")
                    st.session_state.pop(f"form_{reg['id']}", None)
                    st.session_state.pop("vivo_estado", None)
                    st.session_state["vivo_msg"] = f"Guardamos {E.num(len(limpias))} fila{'s' if len(limpias) != 1 else ''}."
                    st.rerun()
                except V.FilasInvalidas as e:
                    st.error("Revisa estas filas: " + "; ".join(
                        f"fila {x['fila'] + 1}: {x['motivo']}" if x["fila"] is not None else x["motivo"]
                        for x in e.errores[:5]))
        if st.session_state.get("vivo_msg"):
            st.success(st.session_state.pop("vivo_msg"), icon=":material/check_circle:")
        try:
            ult = vivo.leer_filas(u["correo"], reg["id"])
        except Exception:  # noqa: BLE001
            ult = []
        if ult:
            with st.expander(f"Últimas filas recibidas ({E.num(len(ult))})"):
                t = pd.DataFrame(ult).sort_values("recibido").tail(50).iloc[::-1]
                t["origen"] = t["origen"].map({"api": "API", "formulario": "Formulario"}).fillna(t["origen"])
                ver = {"fecha": "Fecha", "entidad": S.mayus(nom_ent), "objetivo": "Ventas", "inventario": "Inventario",
                       "precio": "Precio", "origen": "Origen"}
                if not con_ent:
                    ver.pop("entidad")
                st.dataframe(t[list(ver)].rename(columns=ver), hide_index=True, width="stretch")
st.caption(f"{len(lista)} pronóstico{'s' if len(lista) != 1 else ''} guardado{'s' if len(lista) != 1 else ''}")
for reg in lista:
    fi = FRECUENCIAS.get(reg.get("frecuencia") or "D", FRECUENCIAS["D"])
    with st.container(border=True):
        c1, c2 = st.columns([1.6, 2.4], vertical_alignment="center")
        with c1:
            insignias = E.insignia("Abierto ahora", "ok") if reg["id"] == activo else ""
            if reg["id"] in claves_vivo or (reg.get("config") or {}).get("vivo_link"):
                insignias += " " + E.insignia("En vivo", "azul")
            n_esc = len(reg.get("escenarios") or [])
            st.markdown(f"**{reg['nombre']}** &nbsp; {insignias}", unsafe_allow_html=True)
            datos_fr = {"diaria": "diarios", "semanal": "semanales", "mensual": "mensuales",
                        "trimestral": "trimestrales"}.get(fi["nombre"], fi["nombre"])
            n_ent = reg.get("n_entidades") or 0
            detalle = ["1 serie" if n_ent == 1 else f"{E.num(n_ent)} series", f"datos {datos_fr}",
                       f"horizonte {reg.get('horizonte')} {fi['unidad_pl']}",
                       f"error {E.pct(reg['error_pct'])}" if reg.get("error_pct") is not None else None,
                       f"{n_esc} escenario{'s' if n_esc != 1 else ''}" if n_esc else None]
            st.caption(" · ".join(d for d in detalle if d) + f"  \nArchivo {reg.get('archivo_nombre')} · "
                       f"actualizado {fecha_corta(reg.get('actualizado'))}")
        with c2:
            b1, b4, b2, b3 = st.columns(4)
            if b1.button("Abrir", key=f"abrir_{reg['id']}", type="primary", width="stretch"):
                barra = st.progress(0.0, text="Abriendo…")
                try:
                    S.abrir_pronostico(reg, lambda f, t: barra.progress(min(f, 1.0), text=t))
                    barra.empty()
                    st.switch_page("paginas/pronostico.py")
                except Exception as e:  # noqa: BLE001
                    barra.empty()
                    st.error(f"No se pudo abrir: {e}")
            abierto_vivo = st.session_state.get("vivo_panel") == reg["id"]
            if b4.button("En vivo", key=f"vivo_{reg['id']}", width="stretch",
                         type="secondary", icon=":material/expand_less:" if abierto_vivo else ":material/sensors:"):
                st.session_state["vivo_panel"] = None if abierto_vivo else reg["id"]
                st.rerun()
            with b2.popover("Renombrar", width="stretch"):
                nuevo = st.text_input("Nuevo nombre", reg["nombre"], key=f"nom_{reg['id']}")
                if st.button("Guardar", key=f"ren_{reg['id']}"):
                    repo.actualizar(u["correo"], reg["id"], {"nombre": nuevo.strip() or reg["nombre"]})
                    if reg["id"] == activo:
                        st.session_state["registro"]["nombre"] = nuevo.strip()
                    st.rerun()
            with b3.popover("Borrar", width="stretch"):
                st.caption("Se borra el pronóstico, su archivo de datos y sus escenarios. No se puede deshacer.")
                if st.button("Sí, borrar", key=f"del_{reg['id']}", type="primary"):
                    repo.borrar(u["correo"], reg["id"])
                    try:
                        S.almacen_vivo().borrar_todo(u["correo"], reg["id"])
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        S.almacen_persistente().borrar(A.ruta_datos(Rp.id_usuario(u["correo"]), reg["id"]))
                    except Exception:  # noqa: BLE001
                        pass
                    if reg["id"] == activo:
                        st.session_state.pop("registro", None)
                    st.rerun()
        if st.session_state.get("vivo_panel") == reg["id"]:
            panel_vivo(reg)

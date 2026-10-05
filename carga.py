"""Bloque de Inicio para subir o cargar datos: análisis anterior, archivo, link o ejemplo."""

import streamlit as st

import cuenta
import sesion as S
from motor import datos as D


def bloque_carga():
    """Al cargar datos nuevos lleva a 1. Datos; al abrir un análisis anterior, a su pronóstico."""
    ir = False
    _u = cuenta.usuario()
    if _u:
        try:
            _anteriores = S.repositorio().listar(_u["correo"])
        except Exception:  # noqa: BLE001
            _anteriores = []
        if _anteriores:
            with st.container(key="carga_anterior"):
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

    with st.container(key="carga_origen"):
        origen = st.segmented_control("Origen", ["Subir archivo", "Pegar un link", "Usar un ejemplo"],
                                      default="Subir archivo", label_visibility="collapsed", key="origen_datos")
        if origen == "Pegar un link":
            c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
            url = c1.text_input("Link de Google Sheets, o de un Excel o CSV público",
                                placeholder="https://docs.google.com/spreadsheets/d/…", key="link_datos")
            if c2.button("Leer", type="primary", width="stretch", disabled=not url.strip(), key="leer_link"):
                try:
                    nombre_l, contenido_l = S.descargar_link(url.strip())
                    nombre_ds = S.nombre_desde_link(url.strip(), nombre_l)
                    st.session_state["df_raw"] = S.leer(nombre_l, contenido_l)
                    st.session_state["archivo_bytes"] = contenido_l
                    st.session_state["nombre_dataset"] = nombre_ds
                    st.session_state["link_origen"] = {"url": url.strip(), "hash": S.huella_bytes(contenido_l),
                                                       "nombre": nombre_ds}
                    ir = True
                except Exception as e:  # noqa: BLE001
                    st.error(S._msg_error_link(e))
            st.caption(":material/info: En Google Sheets: **Compartir → Cualquier persona con el enlace → Lector**. "
                       + ("Como iniciaste sesión, tu análisis queda conectado a la planilla: cuando agregues filas, el "
                          "pronóstico se pone al día." if cuenta.usuario() else
                          "Inicia sesión para que el análisis quede conectado a la planilla y se actualice solo."))
        elif origen == "Usar un ejemplo":
            opciones = list(S.EJEMPLOS)
            elegido = st.radio("Dataset de ejemplo", opciones, format_func=lambda a: S.EJEMPLOS[a][0],
                               captions=[S.EJEMPLOS[a][1] for a in opciones], label_visibility="collapsed")
            st.caption(":material/info: Datos de ejemplo generados para probar el sistema.")
            if st.button("Cargar ejemplo", type="primary", icon=":material/download:"):
                st.session_state["df_raw"] = S.cargar_ejemplo(elegido)
                st.session_state["nombre_dataset"] = elegido
                st.session_state.pop("link_origen", None)
                ir = True
        else:
            archivo = st.file_uploader("Archivo CSV o Excel", type=["csv", "xlsx", "xls"], label_visibility="collapsed")
            if archivo is not None and archivo.name != st.session_state.get("nombre_dataset"):
                try:
                    st.session_state["df_raw"] = S.leer(archivo.name, archivo.getvalue())
                    st.session_state["archivo_bytes"] = archivo.getvalue()
                    st.session_state["nombre_dataset"] = archivo.name
                    st.session_state.pop("link_origen", None)
                    ir = True
                except Exception as e:  # noqa: BLE001
                    st.error(f"No pudimos leer el archivo: {e}")
    if ir:
        st.switch_page("paginas/datos.py")

# Librerias
import streamlit as st
import cuenta
import estilo as E
import sesion as S

st.set_page_config(page_title=E.NOMBRE_APP, page_icon="favicon.png", layout="wide", initial_sidebar_state="auto")
E.aplicar_estilo()

inicio = st.Page("paginas/inicio.py", title="Inicio", icon=":material/home:", default=True)
p_datos = st.Page("paginas/datos.py", title="1. Datos")
p_act = st.Page("paginas/actualizacion.py", title="Actualización en tiempo real")
p_an = st.Page("paginas/analisis.py", title="Análisis de tus datos")
secciones = {
    "Pronosticar": [
        p_datos,
        st.Page("paginas/pronostico.py", title="2. Pronóstico"),
        st.Page("paginas/decisiones.py", title="3. Decisiones"),
        st.Page("paginas/finanzas.py", title="4. Finanzas"),
        st.Page("paginas/escenarios.py", title="5. Escenarios"),
    ],
}
if cuenta.login_disponible():
    secciones["Tu cuenta"] = [st.Page("paginas/mis_pronosticos.py", title="Mis pronósticos")]
if S.modo_dev():
    secciones["Desarrollador"] = [st.Page("paginas/tecnico.py", title="Detalles técnicos")]
grupo_datos = (p_datos, p_act, p_an)

st.logo("logo.png", size="large")

pg = st.navigation([inicio] + [p for ps in secciones.values() for p in ps] + [p_act, p_an], position="hidden")
with st.sidebar:
    st.page_link(inicio)
cuenta.caja_cuenta()
with st.sidebar:
    for nombre, ps in secciones.items():
        st.markdown(f'<div class="seccion-menu">{nombre}</div>', unsafe_allow_html=True)
        for p in ps:
            if p is p_datos:
                alertas = S.alertas_datos()
                with st.container(key="menu_tus_datos" + ("_activo" if pg.url_path in [x.url_path for x in grupo_datos]
                                                          else "") + ("_alerta" if alertas else "")):
                    st.page_link(p)
                if pg.url_path in [x.url_path for x in grupo_datos]:
                    S.menu_tus_datos(pg, *grupo_datos, alertas)
                continue
            st.page_link(p)
            if p.url_path == pg.url_path:
                S.menu_vistas(p.url_path.split("/")[-1])
    st.divider()
if pg.url_path != p_datos.url_path:
    S.actualizacion_automatica()
S.panel_trabajo()
S.aviso_listo()
pg.run()
E.pie_pagina()

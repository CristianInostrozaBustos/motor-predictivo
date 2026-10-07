# Librerias
import streamlit as st
import cuenta
import estilo as E
import sesion as S

st.set_page_config(page_title="Motor Predictivo", page_icon="favicon.png", layout="wide", initial_sidebar_state="auto")
E.aplicar_estilo()
cuenta.verificar_sesion()

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

st.logo("logo_marca.png", size="large", icon_image="logo.png")

pg = st.navigation([inicio] + [p for ps in secciones.values() for p in ps] + [p_act, p_an], position="hidden")
urls_datos = [x.url_path for x in grupo_datos]


def boton_menu(p, activo, extra=""):
    """Enlace del menú: lleva a la página; si ya está abierta, pliega o despliega su submenú."""
    abierto = f"_sub_{p.url_path or 'inicio'}"
    with st.container(key=f"nav_{p.url_path or 'inicio'}{'_activo' if activo else ''}{extra}"):
        if st.button(p.title, key=f"navbtn_{p.url_path or 'inicio'}", width="stretch",
                     icon=p.icon if p.icon else None):
            if activo:
                st.session_state[abierto] = not st.session_state.get(abierto, True)
                st.rerun()
            st.session_state[abierto] = True
            st.switch_page(p)
    return activo and st.session_state.get(abierto, True)


with st.sidebar:
    boton_menu(inicio, pg.url_path == inicio.url_path)
    for nombre, ps in secciones.items():
        if nombre != "Pronosticar":
            st.markdown(f'<div class="seccion-menu">{nombre}</div>', unsafe_allow_html=True)
        for p in ps:
            if p is p_datos:
                alertas = S.alertas_datos()
                if boton_menu(p, pg.url_path in urls_datos, "_alerta" if alertas else ""):
                    S.menu_tus_datos(pg, *grupo_datos, alertas)
                continue
            if boton_menu(p, p.url_path == pg.url_path):
                S.menu_vistas(p.url_path.split("/")[-1])
cuenta.caja_cuenta()
with st.sidebar:
    st.divider()
if pg.url_path != p_datos.url_path:
    S.actualizacion_automatica()
S.panel_trabajo()
S.aviso_listo()
pg.run()

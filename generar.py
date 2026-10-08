"""Bloque "¿Cuánto quieres pronosticar?": horizonte y botón para generar (página Pronóstico)."""

import pandas as pd
import streamlit as st

import cuenta
import sesion as S
from motor import datos as D


def bloque_horizonte(dp, plan, extra=None):
    """extra(fila): agrega más etiquetas a la misma fila (por ejemplo, ver en unidades o dinero)."""
    fi = dp.freq_info
    clave = S.clave_dataset(dp)
    _, res = S.resultado()
    f1 = dp.df["fecha"].max()
    inicio = f1 + pd.tseries.frequencies.to_offset(D.FRECUENCIAS[dp.config.frecuencia]["pandas"])
    h_prev = min(st.session_state.get("horizonte") or plan.horizonte_defecto, plan.horizonte_max)
    if res is not None:
        # ya pronosticado: fila compacta de etiquetas; el horizonte se ajusta en un desplegable
        h_txt = int(st.session_state.get(f"h_{clave}", h_prev))
        fila = st.container(horizontal=True, vertical_alignment="center", gap="small", key="horizonte_fila")
        with fila.container(key="horizonte_chip", width="content"), st.popover(
                f"{h_txt} {fi['unidad_pl'] if h_txt != 1 else fi['unidad']} hacia adelante",
                width="content"):
            h = st.slider(f"{fi['unidad_pl'].capitalize()} hacia adelante", 1, plan.horizonte_max, h_prev,
                          key=f"h_{clave}")
            st.caption(f"Hasta {plan.horizonte_max} {fi['unidad_pl']} según el historial disponible.")
        st.session_state["horizonte"] = h
        S.actualizar_registro(horizonte=int(h))
        err = S.guardar_pronostico_actual()
        if err:
            st.session_state.setdefault("avisos_almacen", []).append(err)
        info = [f"Pronóstico desde el {inicio:%d/%m/%Y}"]
        if not S.registro_actual() and cuenta.login_disponible() and not cuenta.usuario():
            info.append("Modo abierto: inicia sesión para guardarlo")
        st.session_state["_info_pronostico"] = info
        if extra:
            extra(fila)
        for aviso in st.session_state.pop("avisos_almacen", []):
            if S.modo_dev():
                st.warning(aviso, icon=":material/cloud_off:")
        return
    k_h = f"h_{clave}"
    off = pd.tseries.frequencies.to_offset(D.FRECUENCIAS[dp.config.frecuencia]["pandas"])

    def _fijar(valor):
        st.session_state[k_h] = valor

    if k_h not in st.session_state:
        st.session_state[k_h] = h_prev
    with st.container(key="tarjeta_generar"):
        h_txt = int(st.session_state[k_h])
        fin = inicio + off * (h_txt - 1)
        st.markdown(f'<div class="gen-num">{h_txt}<span>{fi["unidad_pl"] if h_txt != 1 else fi["unidad"]} hacia '
                    f'adelante</span></div><div class="gen-rango">Del {inicio:%d/%m/%Y} al {fin:%d/%m/%Y}</div>',
                    unsafe_allow_html=True)
        h = st.slider(f"{fi['unidad_pl'].capitalize()} hacia adelante", 1, plan.horizonte_max, key=k_h,
                      label_visibility="collapsed")
        rapidos = sorted({v for v in {"D": (30, 90, 180), "W": (4, 12, 26), "M": (3, 6, 12), "Q": (2, 4, 8)}.get(
            dp.config.frecuencia, (4, 12)) if v <= plan.horizonte_max} | {plan.horizonte_max})
        with st.container(horizontal=True, gap="small", key="gen_rapidos"):
            for v in rapidos[-3:]:
                st.button(f"{v} {fi['unidad_pl'] if v != 1 else fi['unidad']}", key=f"gen_r_{v}",
                          type="primary" if v == h else "secondary", on_click=_fijar, args=(v,))
        if S.trabajo_activo() is not None:
            st.caption(":material/cloud_sync: Generando en segundo plano")
            return
        fondo = S.segundo_plano_disponible() and not S.modelo_guardado(clave)
        with st.container(horizontal=True, vertical_alignment="center", gap="medium", key="gen_accion"):
            generar = st.button("Generar pronóstico", type="primary", icon=":material/auto_awesome:", key="btn_generar")
            if S.modelo_guardado(clave):
                st.caption("Ya analizado antes: sale al instante")
            elif fondo:
                st.caption(":material/schedule: 3 a 8 minutos, en segundo plano")
            else:
                st.caption(":material/schedule: 1 a 4 minutos")
        if generar and fondo:
            st.session_state["horizonte"] = h
            err = S.guardar_pronostico_actual(sin_modelo=True)
            r_nuevo = S.registro_actual()
            err = err or (S.encolar(r_nuevo["id"], "nuevo") if r_nuevo else "No se pudo guardar el análisis.")
            if err:
                st.error(err)
            else:
                st.rerun()
        elif generar:
            barra = st.progress(0.0, text="Preparando…")
            res_nuevo, origen = S.entrenar(clave, dp, plan, lambda frac, txt: barra.progress(min(frac, 1.0), text=txt))
            barra.empty()
            st.session_state["resultado"] = {"clave": clave, "res": res_nuevo, "origen": origen}
            st.session_state["horizonte"] = h
            st.rerun()

"""Bloque "¿Cuánto quieres pronosticar?": horizonte y botón para generar (página Pronóstico)."""

import pandas as pd
import streamlit as st

import cuenta
import sesion as S
from motor import datos as D


def bloque_horizonte(dp, plan):
    fi = dp.freq_info
    clave = S.clave_dataset(dp)
    _, res = S.resultado()
    f1 = dp.df["fecha"].max()
    inicio = f1 + pd.tseries.frequencies.to_offset(D.FRECUENCIAS[dp.config.frecuencia]["pandas"])
    h_prev = min(st.session_state.get("horizonte") or plan.horizonte_defecto, plan.horizonte_max)
    if res is not None:
        # ya pronosticado: fila compacta de etiquetas; el horizonte se ajusta en un desplegable
        c1, c2 = st.columns([1, 3.2], vertical_alignment="center")
        h_txt = int(st.session_state.get(f"h_{clave}", h_prev))
        with c1.container(key="horizonte_chip"), st.popover(f"{h_txt} {fi['unidad_pl'] if h_txt != 1 else fi['unidad']} hacia adelante",
                        icon=":material/date_range:", width="stretch"):
            h = st.slider(f"{fi['unidad_pl'].capitalize()} hacia adelante", 1, plan.horizonte_max, h_prev,
                          key=f"h_{clave}")
            st.caption(f"Hasta {plan.horizonte_max} {fi['unidad_pl']} según el historial disponible.")
        st.session_state["horizonte"] = h
        S.actualizar_registro(horizonte=int(h))
        err = S.guardar_pronostico_actual()
        if err:
            st.session_state.setdefault("avisos_almacen", []).append(err)
        reg = S.registro_actual()
        chips = [f"Desde <b>{inicio:%d/%m/%Y}</b>"]
        if reg:
            chips.append(f"Guardado como <b>{reg['nombre']}</b>")
        elif cuenta.login_disponible() and not cuenta.usuario():
            chips.append("Modo abierto: inicia sesión para guardarlo")
        c2.markdown('<div class="resumen-chips" style="margin:0">' + "".join(f"<span>{c}</span>" for c in chips)
                    + "</div>", unsafe_allow_html=True)
        for aviso in st.session_state.pop("avisos_almacen", []):
            if S.modo_dev():
                st.warning(aviso, icon=":material/cloud_off:")
        return
    with st.container(border=True):
        h = st.slider(f"{fi['unidad_pl'].capitalize()} hacia adelante", 1, plan.horizonte_max, h_prev,
                      key=f"h_{clave}")
        st.caption(f"Hasta {plan.horizonte_max} {fi['unidad_pl']} según el historial disponible. "
                   f"El pronóstico empieza el {inicio:%d/%m/%Y}.")
        if S.trabajo_activo() is not None:
            st.caption(":material/cloud_sync: Generando en segundo plano")
            return
        fondo = S.segundo_plano_disponible() and not S.modelo_guardado(clave)
        generar = st.button("Generar pronóstico", type="primary", icon=":material/auto_graph:")
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
        if S.modelo_guardado(clave):
            st.caption(":material/bolt: Estos datos ya se analizaron antes: el pronóstico sale al instante.")
        elif fondo:
            st.caption(":material/cloud_sync: Se entrena en segundo plano (entre 3 y 8 minutos)")
        else:
            st.caption(":material/schedule: Toma entre 1 y 4 minutos según el tamaño de tus datos.")

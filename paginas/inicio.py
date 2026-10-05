import streamlit as st

import estilo as E
from carga import bloque_carga
from sesion import panel_dataset

panel_dataset()

st.markdown(
    f"""
    <div class="portada">
      <div class="marca-portada"><span class="punto"></span>{E.NOMBRE_APP}</div>
      <h1>Anticipa tu demanda.<br><b class="acento">Abastece con precisión.</b></h1>
      <p>Sube tu historial y obtén el pronóstico de cada producto, cuánto y cuándo pedir, y su impacto
      financiero.</p>
      <div class="rasgos">CSV, Excel o Google Sheets<i>·</i>Diario, semanal o mensual<i>·</i>De una a miles de series</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="etiqueta-portada">Empieza con tus datos</div>', unsafe_allow_html=True)
bloque_carga()

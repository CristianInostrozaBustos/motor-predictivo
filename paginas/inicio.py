import streamlit as st

import estilo as E
from carga import bloque_carga
from sesion import panel_dataset

panel_dataset()
st.markdown("""<style>
[data-testid="stMain"] {
  background:
    radial-gradient(55vw 55vw at 92% 4%, rgba(42,120,214,0.45), rgba(42,120,214,0) 62%),
    radial-gradient(38vw 38vw at 78% 78%, rgba(154,123,240,0.30), rgba(154,123,240,0) 65%),
    radial-gradient(34vw 34vw at 48% 66%, rgba(31,182,176,0.20), rgba(31,182,176,0) 65%),
    radial-gradient(40vw 40vw at 4% 100%, rgba(42,120,214,0.22), rgba(42,120,214,0) 65%),
    linear-gradient(160deg, #071433 0%, #0a1f4f 55%, #14205a 100%);
}
[data-testid="stMain"] .portada h1 { color: #ffffff !important; }
[data-testid="stMain"] .portada h1 .acento {
  background: linear-gradient(90deg, #7cc4ff 0%, #5fd3e0 45%, #b9a4ff 100%);
  -webkit-background-clip: text; background-clip: text; color: transparent; }
[data-testid="stMain"] .portada p { color: rgba(235,241,255,0.82); }
[data-testid="stMain"] .portada .rasgos { color: rgba(205,218,245,0.62); }
[data-testid="stMain"] .portada .rasgos i { color: rgba(205,218,245,0.35); }
[data-testid="stMain"] .portada .marca-portada { background: rgba(255,255,255,0.08); color: rgba(235,241,255,0.9);
  border-color: rgba(255,255,255,0.16); backdrop-filter: blur(10px); }
[data-testid="stMain"] .portada .punto { background: #7cc4ff; box-shadow: 0 0 0 4px rgba(124,196,255,0.22); }
[data-testid="stMain"] .etiqueta-portada { color: rgba(205,218,245,0.7); }
[data-testid="stMain"] div[class*="st-key-carga_"] { background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.14);
  border-radius: 16px; padding: 12px 14px; max-width: 760px; backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.12), 0 20px 50px rgba(3,10,30,0.35); }
[data-testid="stMain"] div[class*="st-key-carga_"] label, [data-testid="stMain"] div[class*="st-key-carga_"] p,
[data-testid="stMain"] div[class*="st-key-carga_"] small, [data-testid="stMain"] div[class*="st-key-carga_"] span {
  color: rgba(235,241,255,0.9); }
[data-testid="stMain"] div[class*="st-key-carga_"] button[data-variant="segmented_control"] {
  background: rgba(255,255,255,0.06) !important; border-color: rgba(255,255,255,0.18) !important; }
[data-testid="stMain"] div[class*="st-key-carga_"] button[data-variant="segmented_control"] p {
  color: rgba(235,241,255,0.85) !important; }
[data-testid="stMain"] div[class*="st-key-carga_"] button[data-variant="segmented_control"][aria-checked="true"] {
  background: #ffffff !important; border-color: #ffffff !important; }
[data-testid="stMain"] div[class*="st-key-carga_"] button[data-variant="segmented_control"][aria-checked="true"] p {
  color: #0a1f4f !important; }
[data-testid="stMain"] div[class*="st-key-carga_"] input { color: #0b0b0b; }
[data-testid="stMain"] [data-testid="stFileUploaderDropzone"] { background: rgba(255,255,255,0.06);
  border: 1px dashed rgba(255,255,255,0.22); padding: 8px 12px; }
[data-testid="stMain"] [data-testid="stFileUploaderDropzone"] button { padding: 4px 14px; min-height: 0; }
[data-testid="stMain"] div[class*="st-key-carga_"] button[data-variant="segmented_control"] { padding: 4px 14px; min-height: 0; }
[data-testid="stMain"] div[class*="st-key-carga_"] button[data-variant="segmented_control"] p { font-size: .88rem; }
[data-testid="stMain"] [data-testid="stFileUploaderDropzone"] button span,
[data-testid="stMain"] [data-testid="stFileUploaderDropzone"] button p { color: #0b0b0b; }
[data-testid="stMain"] [data-testid="stCaptionContainer"], [data-testid="stMain"] [data-testid="stCaptionContainer"] p {
  color: rgba(205,218,245,0.7) !important; }
[data-testid="stMain"] hr { border-color: rgba(255,255,255,0.12); }
[data-testid="stMain"] .pie, [data-testid="stMain"] footer, [data-testid="stMain"] [class*="pie"] { color: rgba(205,218,245,0.5); }
[data-testid="stHeader"] { background: transparent; }
@media (max-width: 900px) { [data-testid="stMain"] { background:
    radial-gradient(90vw 90vw at 90% 0%, rgba(42,120,214,0.4), rgba(42,120,214,0) 65%),
    linear-gradient(160deg, #071433 0%, #0a1f4f 60%, #14205a 100%); } }
</style>""", unsafe_allow_html=True)

st.markdown(
    f"""
    <div class="portada">
      <div class="marca-portada"><span class="punto"></span>{E.NOMBRE_APP}</div>
      <h1>Anticipa tu demanda.<br><b class="acento">Abastece con precisión.</b></h1>
      <p>Sube tu historial y obtén el pronóstico de cada producto, cuánto y cuándo pedir, y su impacto
      financiero.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="etiqueta-portada">Empieza con tus datos</div>', unsafe_allow_html=True)
bloque_carga()

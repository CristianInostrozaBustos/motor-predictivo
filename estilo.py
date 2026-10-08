"""Identidad visual del sitio: colores, CSS, plantilla de gráficos y componentes."""

import html as _html
import io
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

NOMBRE_APP = "Motor Predictivo de Abastecimiento"
SUBTITULO_APP = "Pronóstico de demanda y política de inventarios para cualquier dataset"

# ---------------------------------------------------------------- paleta
# Paleta categórica validada para daltonismo (orden fijo, nunca rotado)
TINTA = "#0b0b0b"
TINTA_2 = "#52514e"
TINTA_MUTED = "#898781"
GRILLA = "#e1e0d9"
EJE = "#c3c2b7"
SUPERFICIE = "#ffffff"
FONDO = "#f7f7f5"

AZUL = "#2a78d6"
AZUL_OSCURO = "#1c5cab"
AZUL_BANDA = "rgba(42,120,214,0.16)"
NARANJO = "#eb6834"
AMARILLO = "#eda100"
ROSA = "#e87ba4"
VERDE = "#008300"
AQUA = "#1baf7a"
ROJO = "#d03b3b"

SERIES = [AZUL, NARANJO, AQUA, AMARILLO, ROSA, VERDE, "#4a3aa7", "#e34948"]

MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def color_sku(sku, skus=None):
    """El color sigue al producto, no a su posición en un filtro: se asigna sobre el catálogo completo cargado."""
    dp = st.session_state.get("dp")
    if dp is not None:
        clave = id(dp)
        cache = st.session_state.get("_colores_sku")
        if not cache or cache[0] != clave:
            cache = (clave, {str(e): i for i, e in enumerate(sorted(map(str, dp.df["entidad"].unique())))})
            st.session_state["_colores_sku"] = cache
        i = cache[1].get(str(sku))
        if i is not None:
            return SERIES[i % len(SERIES)]
    lista = sorted(map(str, skus)) if skus is not None else [str(sku)]
    return SERIES[lista.index(str(sku)) % len(SERIES)] if str(sku) in lista else SERIES[0]


def rgba(color_hex, alfa):
    h = color_hex.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alfa})"


# ---------------------------------------------------------------- formato chileno

def num(x, dec=0):
    """1234567.8 -> '1.234.568' ; con dec=2 -> '1.234.567,80'. Sin dato o infinito -> '—'."""
    try:
        if x is None or not np.isfinite(float(x)):
            return "—"
    except (TypeError, ValueError):
        return str(x)
    s = f"{x:,.{dec}f}"
    if s.lstrip("-").strip("0.,") == "":
        s = s.lstrip("-")
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def pct(x, dec=1):
    t = num(x, dec)
    return t if t == "—" else f"{t}%"


def clp(x):
    if num(x) == "—":
        return "—"
    signo = "−" if x < 0 else ""
    return f"{signo}${num(abs(x))}"


def escala_pesos(maximo):
    """Para ejes en $: si los montos son grandes, se grafican en millones (evita "B" o "G" en inglés)."""
    return ((1e6, "Ingresos (millones de $)", "$%{y:,.1f} M") if maximo >= 1e7
            else (1.0, "Ingresos ($)", "$%{y:,.0f}"))


def clp_corto(x):
    """Montos grandes en corto para tarjetas: $339,0 M · $1.432 M · $85.300."""
    if num(x) == "—":
        return "—"
    signo = "−" if x < 0 else ""
    x = abs(x)
    if x >= 1e6:
        m = x / 1e6
        return f"{signo}${num(m, 1) if m < 100 else num(m)} M"
    return f"{signo}${num(x)}"


def clp_md(x):
    """Monto para textos con Markdown (captions, deltas): el $ se escapa para que no se lea como fórmula."""
    return clp(x).replace("$", "\\$")


def mes_es(periodo):
    ts = periodo.to_timestamp() if hasattr(periodo, "to_timestamp") else pd.Timestamp(periodo)
    return f"{MESES_ES[ts.month - 1].capitalize()} {ts.year}"


# ---------------------------------------------------------------- plantilla Plotly

def registrar_plantilla():
    fuente = "Inter, system-ui, -apple-system, Segoe UI, sans-serif"
    t = go.layout.Template()
    t.layout = go.Layout(
        font=dict(family=fuente, size=12, color=TINTA_2),
        title=dict(font=dict(size=15, color=TINTA), x=0, xanchor="left", xref="container", y=1, yref="container", yanchor="top", pad=dict(t=12, l=4)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        colorway=SERIES,
        margin=dict(l=8, r=12, t=40, b=8),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="white", bordercolor=GRILLA, font=dict(family=fuente, size=12, color=TINTA)),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=11, color=TINTA_2), itemsizing="constant"),
        xaxis=dict(showgrid=False, linecolor=EJE, showspikes=True, spikemode="across", spikesnap="cursor",
                   spikecolor="rgba(120,118,110,0.35)", spikethickness=1, spikedash="solid", ticks="outside",
                   tickcolor=EJE, ticklen=4, tickfont=dict(color=TINTA_MUTED, size=11),
                   title=dict(font=dict(color=TINTA_MUTED, size=11)), zeroline=False, automargin=True,
                   separatethousands=True, exponentformat="none"),
        yaxis=dict(gridcolor="#efeee9", gridwidth=1, zeroline=False, linecolor="rgba(0,0,0,0)",
                   tickfont=dict(color=TINTA_MUTED, size=11), title=dict(font=dict(color=TINTA_MUTED, size=11)),
                   separatethousands=True, automargin=True, exponentformat="none"),
        separators=",.",
        barcornerradius=4,
        bargap=0.28,
    )
    t.data.scatter = [go.Scatter(line=dict(width=2), mode="lines")]
    pio.templates["motor"] = t
    pio.templates.default = "motor"


# ---------------------------------------------------------------- CSS

CSS = """
<style>

.block-container { padding-top: 3.4rem; padding-bottom: 3rem; max-width: 1240px; }
header[data-testid="stHeader"] { background: transparent; height: 2.6rem; }
h1, h2, h3 { letter-spacing: -0.02em; color: #0b0b0b; }
h1 { font-weight: 800 !important; }
h2 { font-weight: 700 !important; font-size: 1.45rem !important; }
h3 { font-weight: 650 !important; font-size: 1.12rem !important; }

/* Tarjetas KPI */
[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid rgba(11,11,11,0.08);
    border-radius: 14px;
    padding: 10px 14px 9px 16px;
    box-shadow: 0 1px 3px rgba(11,11,11,0.05), 0 6px 18px rgba(11,11,11,0.04);
    border-left: 4px solid #2a78d6;
}
[data-testid="stMetricLabel"] p { font-size: 0.72rem !important; color: #6b6a66 !important; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.04em; }
[data-testid="stMetricValue"] { font-size: 1.35rem !important; font-weight: 700; color: #12305e; letter-spacing: -0.02em; }
[data-testid="stMetricDelta"] { font-size: 0.78rem !important; }

/* Contenedores con borde como tarjetas blancas */
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) {
    border-radius: 16px;
}
div[data-testid="stVerticalBlockBorderWrapper"] { background: #ffffff; }

/* Sidebar */
section[data-testid="stSidebar"] { background: linear-gradient(180deg, #050c22 0%, #071431 100%); border-right: 1px solid rgba(255,255,255,0.06); }
section[data-testid="stSidebar"] .ficha .v { color: #ffffff; }
section[data-testid="stSidebar"] [data-testid="stPageLink"] a, section[data-testid="stSidebar"] [data-testid="stPageLink"] a span,
section[data-testid="stSidebar"] [data-testid="stPageLink"] a p,
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"], section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
section[data-testid="stSidebar"] [data-testid="stRadio"] label p, section[data-testid="stSidebar"] [data-testid="stHeadingWithActionElements"] {
  color: #e6edff !important; }
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"], section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
  color: rgba(205,218,245,0.6) !important; }
section[data-testid="stSidebar"] [data-testid="stPageLink"] a:hover { background: rgba(255,255,255,0.06); }
section[data-testid="stSidebar"] [data-testid="stPageLink"] a[aria-current="page"],
section[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"][aria-current="page"] { background: rgba(255,255,255,0.08) !important; }
section[data-testid="stSidebar"] .stButton > button[kind="secondary"] { background: rgba(255,255,255,0.06); color: #e6edff;
  border: 1px solid rgba(255,255,255,0.18); }
section[data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover { background: rgba(255,255,255,0.12); color: #ffffff;
  border-color: rgba(255,255,255,0.3); }
section[data-testid="stSidebar"] .stButton > button[kind="secondary"] p { color: inherit; }
section[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.12); }
section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] button,
section[data-testid="stSidebar"] [data-testid="stSidebarHeader"] svg,
section[data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] svg { color: rgba(205,218,245,0.75) !important;
  fill: currentColor; }
section[data-testid="stSidebar"] .ficha .l { color: rgba(205,218,245,0.55); }
section[data-testid="stSidebar"] .marca { padding: 4px 0 10px 0; }

/* Menú lateral hecho a mano */
section[data-testid="stSidebar"] .seccion-menu { font-size: .86rem; font-weight: 600; color: rgba(205,218,245,0.55); margin: 14px 0 0 8px;
  padding-bottom: 2px; line-height: 1.2; }
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"]:has(> .seccion-menu) { margin-bottom: 0; }
section[data-testid="stSidebar"] { width: 340px !important; min-width: 340px !important; }
section[data-testid="stSidebar"] .titulo-controles { font-size: .78rem; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: #2a78d6; margin: 6px 0 2px 2px; padding-top: 12px;
  border-top: 1px solid rgba(11,11,11,0.08); }
.titulo-compacto { margin: 0 0 .6rem 0; }
section[data-testid="stSidebar"] [class*="st-key-vistas_"] { margin: 2px 0 6px 14px; padding-left: 10px;
  border-left: 2px solid rgba(124,196,255,0.35); }
section[data-testid="stSidebar"] [class*="st-key-vistas_"] label p { font-size: .88rem; }
[class*="st-key-panel_control"] { background: #ffffff; }
.titulo-compacto .eyebrow { font-size: .72rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
  color: #2a78d6; }
.titulo-compacto h2 { font-size: 1.65rem; font-weight: 750; margin: 0; padding: 0; line-height: 1.2; }
section[data-testid="stSidebar"] [data-testid="stPageLink"] a { padding: 5px 8px; border-radius: 8px; }
section[data-testid="stSidebar"] [class*="st-key-menu_tus_datos_activo"] [data-testid="stPageLink"] a {
  background: rgba(255,255,255,0.08); }
section[data-testid="stSidebar"] [class*="st-key-nav_"] button { background: transparent !important; border: none !important;
  box-shadow: none !important; justify-content: flex-start; padding: 6px 10px; min-height: 0; border-radius: 9px;
  font-weight: 400; color: #e6edff !important; }
section[data-testid="stSidebar"] [class*="st-key-nav_"] button > div { justify-content: flex-start; }
section[data-testid="stSidebar"] [data-testid="stRadioOption"]:not([data-selected="true"]) > div > div:first-child {
  background: transparent !important; border: 1.5px solid rgba(205,218,245,0.45) !important; }
section[data-testid="stSidebar"] [data-testid="stRadioOption"]:not([data-selected="true"]) > div > div:first-child > div {
  display: none; }
section[data-testid="stSidebar"] [class*="st-key-nav_"] button p { font-size: .95rem; font-weight: 400; color: #e6edff !important; }
section[data-testid="stSidebar"] [class*="st-key-nav_"] button:hover { background: rgba(255,255,255,0.06) !important; }
section[data-testid="stSidebar"] [class*="st-key-nav_"][class*="_activo"] button { background: rgba(255,255,255,0.09) !important; }
section[data-testid="stSidebar"] [class*="st-key-nav_"][class*="_activo"] button p { font-weight: 650; color: #ffffff !important; }
section[data-testid="stSidebar"] [class*="st-key-nav_"][class*="_alerta"] button p::after,
section[data-testid="stSidebar"] [class*="st-key-vistas_datos_alerta"] [role="radiogroup"] > :nth-child(2) [data-testid="stMarkdownContainer"] p::after {
  content: "1"; display: inline-flex; align-items: center; justify-content: center; min-width: 18px; height: 18px;
  margin-left: 8px; padding: 0 5px; border-radius: 999px; background: #e5383b; color: #ffffff; font-size: .7rem;
  font-weight: 700; line-height: 1; vertical-align: middle; box-shadow: 0 0 0 2px #050c22; }
.globo-rojo { display: inline-flex; align-items: center; justify-content: center; min-width: 20px; height: 20px;
  margin-right: 6px; border-radius: 999px; background: #e5383b; color: #ffffff; font-size: .72rem; font-weight: 700; }
section[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding-top: 0.5rem; }
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: 0.35rem; }

/* Botones */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {
    border-radius: 10px; font-weight: 600; padding: 0.5rem 1.1rem;
}

/* Tablas */
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }

/* --- componentes propios --- */
.hero {
    background: linear-gradient(135deg, #0d366b 0%, #1c5cab 55%, #2a78d6 100%);
    border-radius: 22px; padding: 44px 44px 40px 44px; color: #ffffff;
    position: relative; overflow: hidden; margin-bottom: 8px;
}
.hero:after {
    content: ""; position: absolute; right: -60px; top: -60px; width: 320px; height: 320px;
    border-radius: 50%; background: radial-gradient(circle, rgba(237,161,0,0.35), rgba(237,161,0,0) 70%);
}
.hero .eyebrow { font-size: 0.78rem; letter-spacing: 0.12em; text-transform: uppercase; opacity: 0.8; font-weight: 600; }
.hero h1 { color: #ffffff !important; font-size: 2.35rem !important; line-height: 1.15; margin: 10px 0 12px 0; max-width: 760px; padding: 0; }
.hero p { font-size: 1.05rem; opacity: 0.9; max-width: 680px; line-height: 1.55; margin: 0; }
.hero .chips { margin-top: 20px; display: flex; gap: 8px; flex-wrap: wrap; }
.hero .chip { background: rgba(255,255,255,0.14); border: 1px solid rgba(255,255,255,0.22);
    padding: 5px 12px; border-radius: 999px; font-size: 0.8rem; font-weight: 500; }

.portada { position: relative; padding: 4px 0 28px 0; margin-bottom: 4px; }
.portada > * { position: relative; z-index: 1; }
.portada .marca-portada { display: inline-flex; align-items: center; gap: 8px; font-size: .78rem; font-weight: 600;
    letter-spacing: .08em; text-transform: uppercase; color: #52514e; background: #ffffff;
    border: 1px solid rgba(11,11,11,0.08); border-radius: 999px; padding: 6px 14px 6px 10px; }
.portada .punto { width: 8px; height: 8px; border-radius: 50%; background: #2a78d6;
    box-shadow: 0 0 0 4px rgba(42,120,214,0.15); }
.portada h1 { font-size: 3.1rem !important; line-height: 1.08; letter-spacing: -0.02em; font-weight: 800;
    color: #0b0b0b !important; margin: 26px 0 18px 0; padding: 0; }
.portada h1 .acento { color: #1c5cab; font-weight: 800; }
.portada p { font-size: 1.15rem; color: #52514e; max-width: 620px; line-height: 1.55; margin: 0; }
.portada .rasgos { margin-top: 22px; font-size: .88rem; color: #8a8985; }
.portada .rasgos i { font-style: normal; margin: 0 10px; color: #c4c3bf; }
.etiqueta-portada { font-size: .76rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
    color: #8a8985; margin: 8px 0 8px 2px; }

.resumen-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 2px 0 10px 0; }
.resumen-chips span { background: #ffffff; border: 1px solid rgba(11,11,11,0.08); border-radius: 999px;
    padding: 4px 12px; font-size: .85rem; color: #52514e; }
.resumen-chips b { color: #0b0b0b; font-weight: 650; }
[class*="st-key-horizonte_chip"] button, [class*="st-key-chip_"] button { background: #ffffff; border: 1px solid rgba(11,11,11,0.08); border-radius: 999px;
    padding: 3px 12px !important; min-height: 0 !important; height: auto; line-height: 1.4; font-weight: 400; }
[class*="st-key-horizonte_chip"] button p, [class*="st-key-chip_"] button p { color: #1c5cab; font-size: .85rem; font-weight: 600; line-height: 1.4; }
[class*="st-key-horizonte_chip"] button [data-testid="stIconMaterial"], [class*="st-key-chip_"] button [data-testid="stIconMaterial"] { font-size: 1rem; color: #1c5cab; }
[class*="st-key-horizonte_fila"] [data-testid="stMarkdownContainer"] { margin: 0; }
[class*="st-key-fila_"], [class*="st-key-horizonte_fila"] { margin: 0 0 4px 0; gap: 8px !important; row-gap: 8px !important; }
[class*="st-key-fila_"] [data-testid="stButton"] button { padding: 3px 14px !important; min-height: 0 !important; border-radius: 999px; }
[class*="st-key-fila_"] [data-testid="stButton"] button p { font-size: .85rem; }
[class*="st-key-fila_"] [data-testid="stMarkdownContainer"], [class*="st-key-fila_"] [data-testid="stMarkdownContainer"] p { margin: 0; }
[class*="st-key-fila_"] [data-testid="stElementContainer"]:has(.chip-texto) { width: auto !important; }
/* selección múltiple con el mismo aspecto de la lista de puntos (radio) */
[class*="st-key-_pm_"] label > span + div { width: 1rem !important; height: 1rem !important; min-width: 1rem;
    border-radius: 50% !important; border: none !important; background: #e6e8ec !important; }
[class*="st-key-_pm_"] label > span + div svg { display: none; }
[class*="st-key-_pm_"] label:has(input:checked) > span + div {
    background: radial-gradient(circle, #ffffff 0 0.19rem, #1c5cab 0.21rem) !important; }
[class*="st-key-todos_"] { gap: 4px !important; margin-bottom: 2px; }
[class*="st-key-todos_"] button { padding: 0 4px !important; min-height: 0 !important; }
[class*="st-key-todos_"] button p { font-size: .82rem; }
[class*="st-key-puntos_"] { gap: 0.35rem !important; }
/* tarjeta de cada gráfico */
[class*="st-key-tarjeta_"] { background: #ffffff; border: 1px solid rgba(11,11,11,0.07); border-radius: 14px;
    padding: 14px 16px 6px 16px; box-shadow: 0 1px 3px rgba(11,11,11,0.05), 0 6px 18px rgba(11,11,11,0.04); gap: 4px; }
[class*="st-key-tarjeta_"] > div > [data-testid="stMarkdownContainer"] p,
[class*="st-key-tarjeta_"] [data-testid="stMarkdown"] p { font-size: 0.98rem; font-weight: 650; color: #0b0b0b; margin: 0;
    display: flex; align-items: center; gap: 10px; }
[class*="st-key-tarjeta_"] [data-testid="stMarkdown"] span[role="img"] { font-size: 1.05rem; color: #1c5cab;
    background: #e8f0fb; border-radius: 8px; padding: 5px; }
[class*="st-key-tarjeta_"] [data-testid="stMarkdownContainer"] { margin: 0 0 2px 0 !important; }
[class*="st-key-tarjeta_"] [data-testid="stElementContainer"]:has([data-testid="stMarkdown"]) { height: auto !important; }
[data-testid="stVerticalBlock"]:has(> [data-testid="stLayoutWrapper"] > [class*="st-key-tarjeta_"]) {
    border: none !important; background: transparent !important; padding: 0 !important; box-shadow: none !important; }
[class*="st-key-cab_"] { flex-wrap: nowrap !important; }
[class*="st-key-cab_"] > div:first-child { min-width: 0; flex: 1 1 auto; }
[class*="st-key-cab_"] [data-testid="stButton"] button { padding: 2px 6px !important; min-height: 0 !important; }
[class*="st-key-tarjeta_ir_"] { cursor: pointer; transition: border-color .15s, box-shadow .15s; }
[class*="st-key-tarjeta_ir_"]:hover { border-color: rgba(42,120,214,0.45);
    box-shadow: 0 1px 3px rgba(11,11,11,0.06), 0 8px 22px rgba(42,120,214,0.12); }
[class*="st-key-tarjeta_ir_"] .nsewdrag, [class*="st-key-tarjeta_ir_"] .bars path { cursor: pointer !important; }
[class*="st-key-btn_siguiente"] { margin-left: auto; }
[class*="st-key-btn_siguiente"] button { background: #e5484d !important; border-color: #e5484d !important;
    border-radius: 999px; padding: 4px 16px !important; min-height: 0 !important;
    box-shadow: 0 4px 14px rgba(229,72,77,0.30); }
[class*="st-key-btn_siguiente"] button:hover { background: #d03b3b !important; border-color: #d03b3b !important; }
[class*="st-key-btn_siguiente"] button p { color: #ffffff !important; font-weight: 650; font-size: .88rem; }
.st-key-btn_simulado button:disabled { background: #e9f6ee !important; border: 1px solid #b9e2c6 !important;
    border-radius: 999px; opacity: 1 !important; cursor: default; }
.st-key-btn_simulado button:disabled p, .st-key-btn_simulado button:disabled span { color: #1a7f37 !important;
    font-weight: 650; }
body:has(.st-key-esc_desactualizado) :is([class*="st-key-tarjeta_"], .kpis, .nota, [data-testid="stDataFrame"]) {
    opacity: .4; transition: opacity .2s; }
.ia-resumen { font-size: .92rem; line-height: 1.55; color: #1f2937; margin-bottom: 8px; }
.ia-alerta { font-size: .86rem; line-height: 1.45; padding: 7px 12px; border-radius: 10px; margin: 4px 0;
    border-left: 3px solid #e0a100; background: #fdf8ec; color: #3d3320; }
.ia-alerta.ia-alta { border-left-color: #d03b3b; background: #fdf0f0; color: #4a1f1f; }
[class*="st-key-tarjeta_generar"] { background: #ffffff; border: 1px solid rgba(11,11,11,0.07); border-radius: 16px;
    padding: 30px 36px; max-width: 820px; box-shadow: 0 1px 3px rgba(11,11,11,0.05), 0 8px 24px rgba(11,11,11,0.05); }
.gen-num { font-size: 4rem; font-weight: 800; color: #1c5cab; letter-spacing: -0.03em; line-height: 1; }
.gen-num span { font-size: 1.35rem; color: #52514e; font-weight: 600; margin-left: 10px; letter-spacing: 0; }
.gen-rango { color: #6b6a66; font-size: .9rem; margin: 8px 0 4px 2px; }
[class*="st-key-gen_rapidos"] button { border-radius: 999px; padding: 4px 16px !important; min-height: 0 !important; }
[class*="st-key-gen_rapidos"] button p { font-size: .88rem; font-weight: 600; }
[class*="st-key-gen_rapidos"] button[kind="secondary"] p { color: #1c5cab; }
[class*="st-key-gen_accion"] { margin-top: 14px; }
[class*="st-key-btn_generar"] button { background: #e5484d !important; border-color: #e5484d !important; border-radius: 999px;
    padding: 10px 24px !important; box-shadow: 0 6px 18px rgba(229,72,77,0.32); }
[class*="st-key-btn_generar"] button:hover { background: #d03b3b !important; border-color: #d03b3b !important; }
[class*="st-key-btn_generar"] button p { color: #fff !important; font-weight: 650; font-size: 1rem; }
.kpis { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 6px 2px 12px 2px; }
.kpis .kpi { display: flex; align-items: baseline; gap: 6px; font-size: .82rem; color: #6b6a66; }
.kpis .kpi b { color: #12305e; font-weight: 700; font-size: .92rem; }
.kpis .kpi small { color: #8a8984; font-size: .75rem; }
.kpis .kpi[title] { cursor: help; }
.info-pie { font-size: .8rem; color: #8a8984; margin: 2px 0 10px 2px; }
[data-testid="stPopoverBody"] { max-height: 60vh; overflow-y: auto; }
.chip-texto { display: inline-block; font-size: .85rem; color: #6b6a66; padding: 6px 4px; white-space: nowrap; }
.chip-texto.aviso { color: #b45309; }

.encabezado { margin: 4px 0 18px 0; }
.encabezado .eyebrow { font-size: 0.76rem; letter-spacing: 0.1em; text-transform: uppercase; color: #1c5cab; font-weight: 700; }
.encabezado h1 { font-size: 2rem !important; margin: 4px 0 6px 0; padding: 0; }
.encabezado p { color: #52514e; font-size: 1rem; margin: 0; max-width: 820px; line-height: 1.5; }

.paso { background: #ffffff; border: 1px solid rgba(11,11,11,0.08); border-radius: 16px; padding: 20px; height: 100%; }
.paso .n { width: 30px; height: 30px; border-radius: 9px; background: #e8f0fb; color: #1c5cab;
    display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 0.9rem; margin-bottom: 12px; }
.paso h4 { margin: 0 0 6px 0; font-size: 1rem; font-weight: 650; color: #0b0b0b; padding: 0; }
.paso p { margin: 0; color: #52514e; font-size: 0.9rem; line-height: 1.5; }

.sku-card-titulo { display: flex; align-items: center; gap: 8px; font-weight: 650; color: #0b0b0b; font-size: 0.98rem; }
.punto { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.sku-card-sub { color: #898781; font-size: 0.78rem; margin-top: 2px; }
.sku-card-nums { display: flex; gap: 18px; margin-top: 10px; }
.sku-card-nums .v { font-size: 1.2rem; font-weight: 700; color: #0b0b0b; letter-spacing: -0.01em; }
.sku-card-nums .l { font-size: 0.72rem; color: #898781; }

.nota { background: #fbf6e9; border: 1px solid #f1e2b8; border-radius: 12px; padding: 12px 16px;
    color: #5b4a1a; font-size: 0.9rem; line-height: 1.5; }
.ficha { display: flex; flex-direction: column; gap: 2px; }
.ficha .l { font-size: 0.72rem; color: #898781; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
.ficha .v { font-size: 0.95rem; color: #0b0b0b; font-weight: 600; }
.pie { color: #898781; font-size: 0.8rem; text-align: center; margin-top: 36px; padding-top: 16px;
    border-top: 1px solid rgba(11,11,11,0.07); }

@media (max-width: 640px) {
    .hero { padding: 28px 22px; }
    .hero h1 { font-size: 1.6rem !important; }
    .portada { padding: 28px 0 24px 0; }
    .portada h1 { font-size: 2.1rem !important; }
}
</style>
"""


def aplicar_estilo():
    st.markdown(CSS, unsafe_allow_html=True)
    registrar_plantilla()


# ---------------------------------------------------------------- componentes

def titulo_compacto(eyebrow, titulo):
    """Encabezado de una línea para páginas tipo dashboard."""
    st.markdown(f'<div class="titulo-compacto"><div class="eyebrow">{eyebrow}</div><h2>{titulo}</h2></div>',
                unsafe_allow_html=True)


def titulo_controles(texto="Controles"):
    st.markdown(f'<div class="titulo-controles">{texto}</div>', unsafe_allow_html=True)


def encabezado(eyebrow, titulo, descripcion=""):
    st.markdown(
        f'<div class="encabezado"><div class="eyebrow">{eyebrow}</div>'
        f'<h1>{titulo}</h1><p>{descripcion}</p></div>',
        unsafe_allow_html=True,
    )


def nota(texto):
    st.markdown(f'<div class="nota">{texto}</div>', unsafe_allow_html=True)


MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _fechas_en_x(fig):
    """Mínimo y máximo de las fechas del eje x (None si el eje no es de fechas)."""
    lo = hi = None
    for tr in fig.data:
        x = getattr(tr, "x", None)
        if x is None or len(x) == 0:
            continue
        arr = np.asarray([v for v in x if v is not None], dtype=object)
        if len(arr) == 0:
            continue          # trazas vacías (por ejemplo, solo para la leyenda)
        arr = np.asarray(arr.tolist())
        if not (np.issubdtype(arr.dtype, np.datetime64) or
                (arr.dtype == object and isinstance(arr.flat[0], (pd.Timestamp, np.datetime64)))
                or hasattr(arr.flat[0], "year")):
            return None
        s = pd.to_datetime(pd.Series(arr.ravel()), errors="coerce").dropna()
        if s.empty:
            return None
        lo = s.min() if lo is None else min(lo, s.min())
        hi = s.max() if hi is None else max(hi, s.max())
    return (lo, hi) if lo is not None else None


def ticks_fechas_es(lo, hi, max_ticks=8):
    """Marcas del eje x con meses en español (Plotly solo los trae en inglés)."""
    dias = (hi - lo).days
    if dias > 75:
        meses = (hi.year - lo.year) * 12 + hi.month - lo.month + 1
        paso = next(p for p in (1, 2, 3, 4, 6, 12, 24, 36, 60) if meses / p <= max_ticks)
        inicio = pd.Timestamp(lo.year, lo.month, 1)
        if inicio < lo.normalize():
            inicio += pd.DateOffset(months=1)
        if paso >= 12:
            inicio = pd.Timestamp(inicio.year + (inicio.month > 1), 1, 1)
        elif paso > 1:
            while (inicio.month - 1) % paso:
                inicio += pd.DateOffset(months=1)
        vals = list(pd.date_range(inicio, hi, freq=pd.DateOffset(months=paso)))
        textos, anio_prev = [], None
        for v in vals:
            if paso >= 12:
                textos.append(str(v.year))
            elif v.year != anio_prev:
                textos.append(f"{MESES_CORTOS[v.month - 1]}<br>{v.year}")
            else:
                textos.append(MESES_CORTOS[v.month - 1])
            anio_prev = v.year
        return vals, textos
    paso = max(1, int(np.ceil(dias / max_ticks)))
    paso = next((p for p in (1, 2, 3, 7, 14) if p >= paso), paso)
    vals = list(pd.date_range(lo.normalize(), hi, freq=f"{paso}D"))
    return vals, [f"{v.day} {MESES_CORTOS[v.month - 1]}" for v in vals]


def _icono_figura(fig):
    tipos = {t.type for t in fig.data}
    if "bar" in tipos:
        return "bar_chart"
    if fig.data and all(getattr(t, "mode", None) == "markers" for t in fig.data):
        return "scatter_plot"
    return "show_chart"


def _marcar_puntos(fig):
    """Líneas con puntos en los datos cuando hay pocos (mensual, semanal); en series largas solo la línea."""
    for t in fig.data:
        if t.type != "scatter" or t.fill not in (None, "none") or t.showlegend is False:
            continue
        if (t.mode or "lines") != "lines" or (t.line.width is not None and t.line.width == 0):
            continue
        if t.x is not None and len(t.x) <= 60:
            t.mode = "lines+markers"
            t.marker.size = 6
            t.marker.line = dict(color="#ffffff", width=1.5)


def _ir_a_vista(pagina, vista, entidad=None):
    if entidad is not None:
        st.session_state["entidad"] = entidad
        st.session_state["vista_sel"] = [entidad]
    st.session_state[f"vista_{pagina}"] = vista
    st.rerun()


def grafico(fig, key=None, alto=None, icono=None, ir_a=None, eje_entidad=None):
    """Cada gráfico va en su tarjeta: título con ícono arriba a la izquierda y la figura debajo.
    ir_a=(página, vista): al pinchar el gráfico (o la flecha del título) se abre esa vista; con eje_entidad
    ("x" o "y") la barra pinchada además elige ese producto."""
    if alto:
        fig.update_layout(height=alto)
    rango = _fechas_en_x(fig)
    if rango is not None and fig.layout.xaxis.tickvals is None:
        vals, textos = ticks_fechas_es(*rango)
        fig.update_xaxes(tickmode="array", tickvals=vals, ticktext=textos, hoverformat="%d/%m/%Y")
    _marcar_puntos(fig)
    titulo = fig.layout.title.text if fig.layout.title and fig.layout.title.text else None
    hay_leyenda = sum(1 for t in fig.data if t.showlegend is not False and t.name) > 1 or bool(fig.layout.showlegend)
    fig.update_layout(title_text=None, margin=dict(t=34 if hay_leyenda else 10), paper_bgcolor="#ffffff",
                      plot_bgcolor="#ffffff")
    clave = key or str(id(fig))
    with st.container(key=f"tarjeta_{'ir_' if ir_a else ''}{clave}"):
        if titulo or ir_a:
            with st.container(horizontal=True, vertical_alignment="center", gap="small", key=f"cab_{clave}"):
                st.markdown((titulo or "").replace("$", "\\$"), width="stretch")
                if ir_a and st.button("", icon=":material/arrow_forward:", type="tertiary", key=f"ir_{clave}",
                                      help="Ver el detalle"):
                    _ir_a_vista(*ir_a)
        if ir_a:
            ev = st.plotly_chart(fig, width="stretch", key=key, on_select="rerun", selection_mode="points",
                                 config={"displaylogo": False, "locale": "es"})
            puntos = (ev.selection.points if ev and ev.selection else None) or []
            if puntos:
                ent = puntos[0].get(eje_entidad) if eje_entidad else None
                _ir_a_vista(*ir_a, entidad=ent)
        else:
            st.plotly_chart(fig, width="stretch", key=key, config={"displaylogo": False, "locale": "es"})


class Kpis:
    """Junta indicadores con la misma firma de st.metric y los muestra después, en una franja discreta bajo los
    gráficos (son informativos: no van sobre los controles)."""

    def __init__(self):
        self.items = []

    def metric(self, label, value, delta=None, help=None, **_):
        self.items.append((label, value, delta, help))

    def mostrar(self):
        if not self.items:
            return
        partes = []
        for label, value, delta, ayuda in self.items:
            tip = f' title="{_html.escape(str(ayuda))}"' if ayuda else ""
            extra = f"<small>{_html.escape(str(delta))}</small>" if delta else ""
            partes.append(f'<div class="kpi"{tip}><span>{_html.escape(str(label))}</span>'
                          f'<b>{_html.escape(str(value))}</b>{extra}</div>')
        st.markdown('<div class="kpis">' + "".join(partes) + "</div>", unsafe_allow_html=True)
        self.items = []


def excel_bytes(hojas):
    """hojas: dict nombre -> DataFrame. Devuelve el .xlsx en memoria."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for nombre, df in hojas.items():
            df.to_excel(writer, sheet_name=nombre[:31], index=False)
            hoja = writer.sheets[nombre[:31]]
            for col in hoja.columns:
                for c in col[1:]:
                    if hasattr(c.value, "year"):          # fechas en formato chileno
                        c.number_format = "DD/MM/YYYY"
                    elif isinstance(c.value, float):
                        c.number_format = "#,##0" if float(c.value).is_integer() else "#,##0.0"
                ancho = max(len(str(c.value)) if c.value is not None else 0 for c in col)
                hoja.column_dimensions[col[0].column_letter].width = min(40, max(12, ancho + 2))
    return buffer.getvalue()


# ---------------------------------------------------------------- figuras reutilizables

def fig_banda(fechas, p10, p50, p90, nombre_banda="Rango P10–P90", nombre_p50="Pronóstico (P50)", hover="%{y:,.0f} u.",
              color=None):
    """Banda de incertidumbre + línea P50. Devuelve la figura para agregarle más trazas."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=fechas, y=p90, mode="lines", line=dict(width=0), hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=fechas, y=p10, mode="lines", line=dict(width=0), fill="tonexty",
                             fillcolor=rgba(color, 0.16) if color else AZUL_BANDA, name=nombre_banda, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=fechas, y=p50, mode="lines", name=nombre_p50, line=dict(color=color or AZUL, width=2.2),
                             hovertemplate=hover))
    # P10/P90 en el tooltip sin dibujar línea
    fig.add_trace(go.Scatter(x=fechas, y=p90, name="P90", mode="lines", line=dict(width=0),
                             showlegend=False, hovertemplate=hover))
    fig.add_trace(go.Scatter(x=fechas, y=p10, name="P10", mode="lines", line=dict(width=0),
                             showlegend=False, hovertemplate=hover))
    return fig


def destacar_fila(df, columna, valor):
    """Styler que resalta la fila del producto elegido (para st.dataframe y st.data_editor)."""
    estilo = "background-color: #e8f0fb; font-weight: 600"
    return df.style.apply(lambda fila: [estilo if str(fila[columna]) == str(valor) else "" for _ in fila], axis=1)


def insignia(texto, tipo="neutro"):
    colores = {
        "ok": ("#e7f4ea", "#0f6b1f"), "aviso": ("#fbf1dc", "#7a5300"), "error": ("#fbe6e6", "#9b2020"),
        "neutro": ("#eef0f3", "#3d3c39"), "azul": ("#e8f0fb", "#1c5cab"),
    }
    bg, fg = colores[tipo]
    return (f'<span style="background:{bg};color:{fg};padding:2px 9px;border-radius:999px;'
            f'font-size:.76rem;font-weight:600;white-space:nowrap">{texto}</span>')

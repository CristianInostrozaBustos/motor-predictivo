"""Reporte PDF del pronóstico.

reunir() junta lo que el sitio ya calculó (pronóstico, precisión, decisiones, ingresos, último escenario) y
construir_pdf() lo arma con gráficos y párrafos explicativos. Los párrafos salen de explicar_*; son plantillas sobre
los números y se pueden reemplazar por textos redactados por IA sin tocar el resto.
"""

from __future__ import annotations

import io
import os
import re

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.dates  # noqa: E402
import matplotlib.ticker  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.enums import TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import (CondPageBreak, Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer,  # noqa: E402
                                Table, TableStyle)

import estilo as E  # noqa: E402

SECCIONES = ["Resumen", "Pronóstico por producto", "Precisión", "Decisiones de inventario", "Ingresos proyectados",
             "Último escenario"]
MAX_GRAFICOS = 12
AZUL, AZUL_OSCURO, GRIS, TINTA, ROJO = "#2a78d6", "#1c5cab", "#6b7280", "#111827", "#d03b3b"


# ---------------------------------------------------------------- formato
def _t(x) -> str:
    """Texto seguro para el PDF (escapa < > & y usa guion simple)."""
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("−", "-")


def _fuentes():
    try:
        pdfmetrics.getFont("Rep")
        return "Rep", "Rep-B"
    except KeyError:
        pass
    base = os.path.join(os.path.dirname(font_manager.findfont("DejaVu Sans")))
    try:
        pdfmetrics.registerFont(TTFont("Rep", os.path.join(base, "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("Rep-B", os.path.join(base, "DejaVuSans-Bold.ttf")))
        return "Rep", "Rep-B"
    except Exception:  # noqa: BLE001
        return "Helvetica", "Helvetica-Bold"


def _estilos():
    f, fb = _fuentes()
    return {
        "titulo": ParagraphStyle("titulo", fontName=fb, fontSize=22, leading=27, textColor=colors.HexColor(TINTA)),
        "sub": ParagraphStyle("sub", fontName=f, fontSize=10.5, leading=15, textColor=colors.HexColor(GRIS)),
        "h1": ParagraphStyle("h1", fontName=fb, fontSize=14, leading=19, spaceBefore=6, spaceAfter=6,
                             textColor=colors.HexColor(AZUL_OSCURO)),
        "h2": ParagraphStyle("h2", fontName=fb, fontSize=11, leading=15, spaceBefore=4, spaceAfter=2,
                             textColor=colors.HexColor(TINTA)),
        "p": ParagraphStyle("p", fontName=f, fontSize=9.5, leading=14, alignment=TA_LEFT,
                            textColor=colors.HexColor("#1f2937"), spaceAfter=6),
        "nota": ParagraphStyle("nota", fontName=f, fontSize=8, leading=11, textColor=colors.HexColor(GRIS)),
        "celda": ParagraphStyle("celda", fontName=f, fontSize=8, leading=10),
        "celda_b": ParagraphStyle("celda_b", fontName=fb, fontSize=8, leading=10, textColor=colors.white),
        "kpi_v": ParagraphStyle("kpi_v", fontName=fb, fontSize=15, leading=18, textColor=colors.HexColor(AZUL_OSCURO)),
        "kpi_l": ParagraphStyle("kpi_l", fontName=f, fontSize=8, leading=10, textColor=colors.HexColor(GRIS)),
    }


# ---------------------------------------------------------------- reunir datos del sitio
def reunir(secciones) -> dict:
    """Datos del análisis activo para el reporte. Usa lo que el sitio ya calculó (no reentrena)."""
    import streamlit as st

    import sesion as S
    dp, res = S.resultado()
    H = S.horizonte()
    fut = S.pronostico(H)
    fi = dp.freq_info
    ents = list(fut)
    skus = sorted(map(str, dp.df["entidad"].unique()))
    met = res.metricas_entidad.set_index("entidad")
    ef = S.clima_efecto()
    d = {
        "secciones": list(secciones),
        "nombre": st.session_state.get("nombre_dataset", "dataset"),
        "entidad": S.nombre_entidad(dp), "entidades_pl": S.nombre_entidad(dp, True),
        "objetivo": dp.etiquetas.get("objetivo", "demanda"),
        "frecuencia": fi["nombre"], "unidad": fi["unidad"], "unidad_pl": fi["unidad_pl"], "dias_periodo": fi["dias"],
        "hist_desde": dp.df["fecha"].min(), "hist_hasta": dp.df["fecha"].max(),
        "horizonte": H, "generado": pd.Timestamp.now(),
        "clima": dp.clima_lugar.split(",")[0] if getattr(dp, "clima_diaria", None) is not None else None,
        "clima_usa": sorted(ef.loc[ef["usa"], "entidad"].unique()) if ef is not None and len(ef) else [],
        "productos": [],
    }
    largo_hist = int(min(max(3 * H, 2 * {"D": 7, "W": 13, "M": 12, "Q": 4}.get(dp.config.frecuencia, 12)), 730))
    for e in ents:
        g = dp.df[dp.df["entidad"] == e].sort_values("fecha")
        m = met.loc[e] if e in met.index else None
        f = fut[e].iloc[:H]
        anterior = g["objetivo"].iloc[-H:] if len(g) >= H else g["objetivo"]
        d["productos"].append({
            "entidad": str(e), "clave": e, "color": E.color_sku(e, skus),
            "hist": g[["fecha", "objetivo"]].iloc[-largo_hist:].reset_index(drop=True),
            "fut": f.reset_index(drop=True),
            "total": float(f["P50"].sum()), "total_p10": float(f["P10"].sum()), "total_p90": float(f["P90"].sum()),
            "anterior": float(anterior.sum()), "periodos_anterior": int(len(anterior)),
            "wape": float(m["wape"]) if m is not None else np.nan,
            "wape_naive": float(m["wape_naive"]) if m is not None and "wape_naive" in m else np.nan,
            "cobertura": float(m["cobertura"]) if m is not None and "cobertura" in m else np.nan,
            "clima": str(e) in map(str, d["clima_usa"]),
        })
    d["productos"].sort(key=lambda p: -p["total"])

    if "Decisiones de inventario" in secciones:
        from motor import politica as P
        tabla, nivel, revision = S.politica_actual(dp, ents)
        tabla = tabla.set_index("entidad")
        fut_max = S.pronostico(res.plan.horizonte_max)
        filas = []
        for e in ents:
            inv = tabla.loc[e, "inventario"] if e in tabla.index else np.nan
            lt = float(tabla.loc[e, "lead_time"]) if e in tabla.index else 14.0
            par = P.Parametros(lead_time_dias=lt, revision_dias=float(revision), nivel_servicio=nivel,
                               inventario_actual=None if pd.isna(inv) else float(inv), errores=S.errores_modelo(e))
            de = P.decidir(e, fut_max[e], dp.config.frecuencia, par)
            filas.append({"entidad": str(e), "estado": de.estado, "inventario": de.inventario, "ss": de.ss,
                          "rop": de.rop, "cantidad": de.cantidad if de.estado != "Sin inventario" else np.nan,
                          "fecha": de.fecha_pedido, "cobertura": de.cobertura_dias, "lead_time": lt})
        d["decisiones"] = {"nivel": nivel, "revision": int(revision), "filas": filas}

    if "Ingresos proyectados" in secciones and S.hay_precios():
        filas = []
        for p in d["productos"]:
            pr = S.precio(p["clave"])
            if not pr:
                continue
            f = p["fut"]
            infl = S.factor_inflacion(f["fecha"])
            filas.append({"entidad": p["entidad"], "color": p["color"], "precio": pr,
                          "ingresos": float((f["P50"].to_numpy() * pr * infl).sum()),
                          "ingresos_p10": float((f["P10"].to_numpy() * pr * infl).sum()),
                          "ingresos_p90": float((f["P90"].to_numpy() * pr * infl).sum())})
        d["ingresos"] = {"filas": filas, "nota_inflacion": S.nota_inflacion()} if filas else None

    esc = st.session_state.get("_reporte_escenario")
    if "Último escenario" in secciones and esc and esc.get("clave") == S.clave_dataset(dp):
        d["escenario"] = esc
    return d


# ---------------------------------------------------------------- textos explicativos
def _fechas(f0, f1):
    return f"{pd.Timestamp(f0):%d/%m/%Y} al {pd.Timestamp(f1):%d/%m/%Y}"


def explicar_resumen(d) -> str:
    n = len(d["productos"])
    tot = sum(p["total"] for p in d["productos"])
    ant = sum(p["anterior"] for p in d["productos"])
    f = d["productos"][0]["fut"]["fecha"] if d["productos"] else []
    txt = (f"Este reporte resume el pronóstico de {_t(d['objetivo']).lower()} para {n} {_t(d['entidades_pl'] if n != 1 else d['entidad'])} "
           f"durante los próximos {d['horizonte']} {d['unidad_pl']}")
    if len(f):
        txt += f", del {_fechas(f.iloc[0], f.iloc[-1])}"
    txt += (f". El modelo aprendió de tu historial entre {pd.Timestamp(d['hist_desde']):%m/%Y} y "
            f"{pd.Timestamp(d['hist_hasta']):%m/%Y} y espera un total de {E.num(tot)} unidades.")
    if ant > 0:
        cambio = (tot / ant - 1) * 100
        txt += (f" Eso es {E.pct(abs(cambio))} {'más' if cambio >= 0 else 'menos'} que en los últimos "
                f"{d['horizonte']} {d['unidad_pl']} del historial.")
    if d["productos"] and n > 1:
        top = d["productos"][0]
        txt += f" {_t(top['entidad'])} concentra la mayor parte, con {E.pct(top['total'] / tot * 100 if tot else 0)} del total."
    if d.get("clima"):
        usa = d["clima_usa"]
        txt += (f" Los próximos días se ajustan con el pronóstico del tiempo de {_t(d['clima'])} en "
                f"{len(usa)} {_t(d['entidades_pl'] if len(usa) != 1 else d['entidad'])}." if usa else
                f" Se revisó el clima de {_t(d['clima'])}, pero no mejoró la precisión, así que no se usa.")
    return txt


def explicar_producto(d, p) -> str:
    f = p["fut"]
    txt = (f"Se esperan <b>{E.num(p['total'])}</b> unidades en los próximos {d['horizonte']} {d['unidad_pl']}, "
           f"con un rango probable entre {E.num(p['total_p10'])} y {E.num(p['total_p90'])}.")
    if p["anterior"] > 0 and p["periodos_anterior"] == d["horizonte"]:
        c = (p["total"] / p["anterior"] - 1) * 100
        if abs(c) < 3:
            txt += f" Es similar a lo registrado en los últimos {d['horizonte']} {d['unidad_pl']}."
        else:
            txt += (f" Es {E.pct(abs(c))} {'más' if c > 0 else 'menos'} que en los últimos {d['horizonte']} "
                    f"{d['unidad_pl']}.")
    if len(f) > 2:
        i = int(f["P50"].to_numpy().argmax())
        txt += f" El punto más alto se espera el {pd.Timestamp(f['fecha'].iloc[i]):%d/%m/%Y}."
    if np.isfinite(p["wape"]):
        txt += f" En la prueba con datos pasados, el error promedio fue {E.pct(p['wape'])}."
    if p["clima"]:
        txt += " Los primeros días incluyen el ajuste por el pronóstico del tiempo."
    return txt


def explicar_precision(d) -> str:
    ps = [p for p in d["productos"] if np.isfinite(p["wape"])]
    if not ps:
        return "No hay prueba con datos pasados para medir la precisión."
    pesos = np.array([max(p["total"], 1e-9) for p in ps])
    w = float(np.average([p["wape"] for p in ps], weights=pesos))
    mejor = min(ps, key=lambda p: p["wape"])
    peor = max(ps, key=lambda p: p["wape"])
    txt = (f"Para medir la precisión, el modelo pronosticó un tramo del pasado que no vio al entrenar y se comparó con "
           f"lo que realmente ocurrió. El error promedio ponderado por volumen fue <b>{E.pct(w)}</b>.")
    if len(ps) > 1:
        txt += (f" El más preciso fue {_t(mejor['entidad'])} con {E.pct(mejor['wape'])} y el más difícil "
                f"{_t(peor['entidad'])} con {E.pct(peor['wape'])}.")
    nv = [p for p in ps if np.isfinite(p["wape_naive"]) and p["wape_naive"] > 0]
    if nv:
        mej = float(np.mean([1 - p["wape"] / p["wape_naive"] for p in nv]) * 100)
        txt += (f" Frente a repetir lo ocurrido en la temporada anterior, el modelo reduce el error en {E.pct(mej)} en promedio."
                if mej > 0 else " En promedio no supera a repetir lo ocurrido en la temporada anterior, señal de que la "
                                "serie es muy irregular.")
    return txt


def explicar_decisiones(dec, d) -> str:
    filas = dec["filas"]
    riesgo = [f for f in filas if f["estado"] == "Riesgo de quiebre"]
    ahora = [f for f in filas if f["estado"] == "Pedir ahora"]
    ok = [f for f in filas if f["estado"] == "Stock suficiente"]
    sin = [f for f in filas if f["estado"] == "Sin inventario"]
    txt = (f"Con un nivel de servicio de {dec['nivel']} y pedidos que cubren {dec['revision']} días, el sistema calcula "
           f"el stock de seguridad y el punto de reorden de cada {_t(d['entidad'])} a partir del pronóstico y de su error real.")
    partes = []
    if riesgo:
        partes.append(f"{len(riesgo)} en riesgo de quiebre")
    if ahora:
        partes.append(f"{len(ahora)} deben pedir ahora")
    if ok:
        partes.append(f"{len(ok)} tienen stock suficiente")
    if partes:
        txt += " Hoy " + ", ".join(partes[:-1]) + (" y " if len(partes) > 1 else "") + partes[-1] + "."
    if sin:
        txt += f" A {len(sin)} les falta el inventario actual; ingrésalo en Decisiones para obtener su pedido."
    if riesgo:
        txt += " Conviene revisar primero: " + ", ".join(_t(f["entidad"]) for f in riesgo[:5]) + "."
    return txt


def explicar_ingresos(ing, d) -> str:
    tot = sum(f["ingresos"] for f in ing["filas"])
    lo = sum(f["ingresos_p10"] for f in ing["filas"])
    hi = sum(f["ingresos_p90"] for f in ing["filas"])
    txt = (f"Multiplicando el pronóstico por el precio de venta, los ingresos esperados en los próximos {d['horizonte']} "
           f"{d['unidad_pl']} son <b>{E.clp(tot)}</b>, en un rango probable de {E.clp(lo)} a {E.clp(hi)}.")
    if len(ing["filas"]) > 1:
        top = max(ing["filas"], key=lambda f: f["ingresos"])
        txt += f" {_t(top['entidad'])} aporta {E.pct(top['ingresos'] / tot * 100 if tot else 0)}."
    return txt + " " + _t(ing["nota_inflacion"])


def explicar_escenario(esc, d) -> str:
    filas = esc["filas"]
    b = sum(f["demanda_base_evento"] for f in filas)
    c = sum(f["demanda_esc_evento"] for f in filas)
    txt = f"Se simuló <b>{_t(esc['descripcion'])}</b> del {_fechas(esc['desde'], esc['hasta'])}."
    if b > 0:
        cambio = (c / b - 1) * 100
        txt += (f" La demanda durante el evento pasa de {E.num(b)} a {E.num(c)} unidades, "
                f"{E.pct(abs(cambio))} {'más' if cambio >= 0 else 'menos'}." if abs(cambio) >= 0.5 else
                " La demanda durante el evento casi no cambia.")
    if esc.get("con_inventario"):
        sin = sum(f.get("perdida_extra_sin", 0) for f in filas)
        aj = sum(f.get("perdida_extra_aj", 0) for f in filas)
        if sin > 0:
            txt += (f" Si no se reacciona, quedan {E.num(sin)} unidades sin atender por falta de stock; ajustando "
                    f"la política a tiempo bajan a {E.num(aj)}.")
        else:
            txt += " El inventario alcanza a cubrir el evento sin quiebres adicionales."
    return txt


# ---------------------------------------------------------------- datos y borradores para la IA
def _r(x, dec=1):
    try:
        if x is None or not np.isfinite(float(x)):
            return None
        return int(round(float(x))) if dec == 0 else round(float(x), dec)
    except (TypeError, ValueError):
        return None


def compacto(d: dict) -> dict:
    """Resumen sin tablas crudas: solo las cifras que explican el pronóstico."""
    out = {
        "unidad_de_tiempo": d["unidad_pl"], "horizonte": d["horizonte"], "cada_serie_es": d["entidad"],
        "variable": d["objetivo"], "historial": f"{pd.Timestamp(d['hist_desde']):%d/%m/%Y} a "
                                               f"{pd.Timestamp(d['hist_hasta']):%d/%m/%Y}",
        "clima": ({"lugar": d["clima"], "ajusta_a": d["clima_usa"]} if d.get("clima") else None),
        "series": [],
    }
    for p in d["productos"][:40]:
        f = p["fut"]
        i = int(f["P50"].to_numpy().argmax()) if len(f) else 0
        out["series"].append({
            "nombre": p["entidad"], "demanda_esperada": _r(p["total"], 0),
            "rango_probable": [_r(p["total_p10"], 0), _r(p["total_p90"], 0)],
            "mismo_largo_reciente": _r(p["anterior"], 0) if p["periodos_anterior"] == d["horizonte"] else None,
            "error_modelo_pct": _r(p["wape"]), "error_repitiendo_temporada_pct": _r(p["wape_naive"]),
            "reales_dentro_del_rango_pct": _r(p["cobertura"]),
            "fecha_pico": f"{pd.Timestamp(f['fecha'].iloc[i]):%d/%m/%Y}" if len(f) else None,
            "ajustado_por_clima": p["clima"], "periodos_de_historia": int(len(p["hist"])),
        })
    if len(d["productos"]) > 40:
        out["series_omitidas"] = len(d["productos"]) - 40
    if d.get("decisiones"):
        dec = d["decisiones"]
        out["inventario"] = {"nivel_servicio": dec["nivel"], "dias_por_pedido": dec["revision"], "series": [
            {"nombre": f["entidad"], "estado": f["estado"], "inventario": _r(f["inventario"], 0),
             "stock_seguridad": _r(f["ss"], 0), "punto_reorden": _r(f["rop"], 0), "pedido": _r(f["cantidad"], 0),
             "fecha_pedido": f"{pd.Timestamp(f['fecha']):%d/%m/%Y}" if f["fecha"] is not None else None,
             "dias_cubiertos": _r(f["cobertura"], 0), "lead_time_dias": _r(f["lead_time"])}
            for f in dec["filas"][:40]]}
    if d.get("ingresos"):
        out["ingresos"] = [{"nombre": f["entidad"], "precio": _r(f["precio"], 0), "ingresos": _r(f["ingresos"], 0),
                            "rango": [_r(f["ingresos_p10"], 0), _r(f["ingresos_p90"], 0)]}
                           for f in d["ingresos"]["filas"][:40]]
    if d.get("escenario"):
        e = d["escenario"]
        out["escenario"] = {"descripcion": e["descripcion"], "desde": f"{pd.Timestamp(e['desde']):%d/%m/%Y}",
                            "hasta": f"{pd.Timestamp(e['hasta']):%d/%m/%Y}", "series": e["filas"]}
    return out


def borradores(d: dict) -> dict:
    """Textos automáticos de cada sección incluida, sin marcas HTML."""
    sec, b = d["secciones"], {}
    if "Resumen" in sec:
        b["resumen"] = explicar_resumen(d)
    if "Pronóstico por producto" in sec:
        for p in d["productos"][:MAX_GRAFICOS]:
            b[f"producto:{p['entidad']}"] = explicar_producto(d, p)
    if "Precisión" in sec:
        b["precision"] = explicar_precision(d)
    if d.get("decisiones") and "Decisiones de inventario" in sec:
        b["decisiones"] = explicar_decisiones(d["decisiones"], d)
    if d.get("ingresos") and "Ingresos proyectados" in sec:
        b["ingresos"] = explicar_ingresos(d["ingresos"], d)
    if d.get("escenario") and "Último escenario" in sec:
        b["escenario"] = explicar_escenario(d["escenario"], d)
    return {k: re.sub(r"<[^>]+>", "", v).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            for k, v in b.items()}


# ---------------------------------------------------------------- gráficos
def _png(fig) -> io.BytesIO:
    b = io.BytesIO()
    fig.savefig(b, format="png", dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    b.seek(0)
    return b


def _ejes(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#d1d5db")
    ax.tick_params(colors=GRIS, labelsize=7.5)
    ax.grid(axis="y", color="#eef0f3", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: E.num(v)))


def _fecha_corta(v, _=None):
    f = matplotlib.dates.num2date(v)
    mes = E.MESES_CORTOS[f.month - 1]
    if f.day == 1:
        return str(f.year) if f.month == 1 else mes
    return f"{f.day} {mes}"


def grafico_producto(p, ancho_cm=17.0, alto_cm=5.6):
    fig, ax = plt.subplots(figsize=(ancho_cm / 2.54, alto_cm / 2.54))
    h, f = p["hist"], p["fut"]
    ax.plot(h["fecha"], h["objetivo"], color="#9ca3af", linewidth=1.0, label="Historial")
    ax.fill_between(f["fecha"], f["P10"], f["P90"], color=p["color"], alpha=0.16, linewidth=0, label="Rango probable")
    ax.plot(f["fecha"], f["P50"], color=p["color"], linewidth=1.8, label="Pronóstico")
    if len(h):
        ax.plot([h["fecha"].iloc[-1], f["fecha"].iloc[0]], [h["objetivo"].iloc[-1], f["P50"].iloc[0]],
                color=p["color"], linewidth=1.0, linestyle=":")
    _ejes(ax)
    ax.set_ylim(bottom=0)
    loc = matplotlib.dates.AutoDateLocator(minticks=4, maxticks=8)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(_fecha_corta))
    ax.legend(loc="upper left", fontsize=7, frameon=False, ncol=3)
    return _png(fig)


def grafico_barras(nombres, valores, colores, formato, ancho_cm=17.0, formato_eje=None):
    alto = max(3.0, 0.55 * len(nombres) + 1.0)
    fig, ax = plt.subplots(figsize=(ancho_cm / 2.54, alto / 2.54))
    y = np.arange(len(nombres))[::-1]
    ax.barh(y, valores, color=colores, height=0.62)
    _ejes(ax)
    ax.set_yticks(y, [str(n)[:28] for n in nombres])
    for yi, v in zip(y, valores):
        ax.text(v, yi, "  " + formato(v), va="center", fontsize=7.5, color=TINTA)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color="#eef0f3", linewidth=0.8)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: (formato_eje or E.num)(v)))
    ax.set_xlim(0, max(valores) * 1.22 if len(valores) and max(valores) > 0 else 1)
    return _png(fig)


# ---------------------------------------------------------------- PDF
def _tabla(st_, encabezado, filas, anchos):
    datos = [[Paragraph(_t(c), st_["celda_b"]) for c in encabezado]]
    datos += [[Paragraph(_t(c), st_["celda"]) for c in f] for f in filas]
    t = Table(datos, colWidths=[a * cm for a in anchos], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(AZUL_OSCURO)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7fa")]),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e5e7eb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _kpis(st_, pares):
    celdas = [[Paragraph(_t(v), st_["kpi_v"]) for _, v in pares], [Paragraph(_t(k), st_["kpi_l"]) for k, _ in pares]]
    t = Table(celdas, colWidths=[17.0 / len(pares) * cm] * len(pares))
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f3f6fb")),
                           ("TOPPADDING", (0, 0), (-1, 0), 8), ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
                           ("LEFTPADDING", (0, 0), (-1, -1), 10)]))
    return t


def construir_pdf(d: dict, textos: dict | None = None) -> bytes:
    """PDF del reporte. textos: párrafos alternativos por clave (resumen, precision, decisiones, ingresos,
    escenario, producto:<entidad>) para reemplazar los automáticos."""
    textos = {k: _t(v) for k, v in (textos or {}).items() if v}
    st_ = _estilos()
    sec = d["secciones"]
    buf = io.BytesIO()
    fuente = st_["nota"].fontName

    def pie(canvas, doc):
        canvas.saveState()
        canvas.setFont(fuente, 7.5)
        canvas.setFillColor(colors.HexColor(GRIS))
        canvas.drawString(2 * cm, 1.2 * cm, f"Motor Predictivo · {d['nombre']}")
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Página {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                            bottomMargin=2 * cm, title=f"Reporte de pronóstico · {d['nombre']}", author="Motor Predictivo")
    h = []
    h.append(Paragraph("Reporte de pronóstico", st_["titulo"]))
    h.append(Paragraph(f"{_t(d['nombre'])} · generado el {d['generado']:%d/%m/%Y %H:%M}", st_["sub"]))
    h.append(Spacer(1, 10))

    tot = sum(p["total"] for p in d["productos"])
    ps = [p for p in d["productos"] if np.isfinite(p["wape"])]
    w = float(np.average([p["wape"] for p in ps], weights=[max(p["total"], 1e-9) for p in ps])) if ps else np.nan
    if "Resumen" in sec:
        h.append(_kpis(st_, [("Demanda esperada", E.num(tot)), ("Horizonte", f"{d['horizonte']} {d['unidad_pl']}"),
                             (_mayus(d["entidades_pl"]), E.num(len(d["productos"]))), ("Error promedio", E.pct(w))]))
        h.append(Spacer(1, 10))
        h.append(Paragraph("Resumen", st_["h1"]))
        h.append(Paragraph(textos.get("resumen") or explicar_resumen(d), st_["p"]))
        if len(d["productos"]) > 1:
            top = d["productos"][:15]
            h.append(Image(grafico_barras([p["entidad"] for p in top], [p["total"] for p in top],
                                          [p["color"] for p in top], E.num),
                           width=17 * cm, height=max(3.0, 0.55 * len(top) + 1.0) * cm))
            h.append(Paragraph(f"Demanda esperada por {_t(d['entidad'])}"
                               + (", los 15 de mayor volumen." if len(d["productos"]) > 15 else "."), st_["nota"]))

    if "Pronóstico por producto" in sec:
        h.append(Spacer(1, 16))
        h.append(CondPageBreak(9 * cm))
        h.append(Paragraph(f"Pronóstico por {_t(d['entidad'])}", st_["h1"]))
        for p in d["productos"][:MAX_GRAFICOS]:
            bloque = [Paragraph(_t(p["entidad"]), st_["h2"]),
                      Image(grafico_producto(p), width=17 * cm, height=5.6 * cm),
                      Paragraph(textos.get(f"producto:{p['entidad']}") or explicar_producto(d, p), st_["p"]),
                      Spacer(1, 6)]
            h.append(KeepTogether(bloque))
        if len(d["productos"]) > MAX_GRAFICOS:
            h.append(Paragraph(f"Se muestran los {MAX_GRAFICOS} de mayor volumen. El detalle de los "
                               f"{len(d['productos']) - MAX_GRAFICOS} restantes está en la tabla de Precisión y en el "
                               "Excel descargable del sitio.", st_["nota"]))

    if "Precisión" in sec and ps:
        h.append(Spacer(1, 16))
        h.append(CondPageBreak(9 * cm))
        h.append(Paragraph("Precisión del modelo", st_["h1"]))
        h.append(Paragraph(textos.get("precision") or explicar_precision(d), st_["p"]))
        filas = [[p["entidad"], E.num(p["total"]), E.pct(p["wape"]),
                  E.pct(p["wape_naive"]) if np.isfinite(p["wape_naive"]) else "—",
                  E.pct(p["cobertura"]) if np.isfinite(p["cobertura"]) else "—"] for p in d["productos"]]
        h.append(_tabla(st_, [_mayus(d["entidad"]), "Demanda esperada", "Error del modelo", "Error repitiendo temporada",
                              "Reales dentro del rango"], filas, [4.6, 3.0, 3.0, 3.4, 3.0]))
        h.append(Spacer(1, 4))
        h.append(Paragraph("Error = suma de errores absolutos sobre la demanda real del tramo de prueba. El rango "
                           "probable debería contener cerca del 80% de los valores reales.", st_["nota"]))

    dec = d.get("decisiones")
    if "Decisiones de inventario" in sec and dec:
        h.append(Spacer(1, 16))
        h.append(CondPageBreak(9 * cm))
        h.append(Paragraph("Decisiones de inventario", st_["h1"]))
        h.append(Paragraph(textos.get("decisiones") or explicar_decisiones(dec, d), st_["p"]))
        orden = {"Riesgo de quiebre": 0, "Pedir ahora": 1, "Stock suficiente": 2, "Sin inventario": 3}
        filas = [[f["entidad"], f["estado"], E.num(f["inventario"]) if f["inventario"] is not None else "—",
                  E.num(f["ss"]), E.num(f["rop"]), E.num(f["cantidad"]),
                  f"{pd.Timestamp(f['fecha']):%d/%m/%Y}" if f["fecha"] is not None else "—",
                  E.num(f["cobertura"]) if f["cobertura"] is not None else "—"]
                 for f in sorted(dec["filas"], key=lambda f: (orden.get(f["estado"], 9), f["entidad"]))]
        h.append(_tabla(st_, [_mayus(d["entidad"]), "Estado", "Stock", "Seguridad", "Reorden",
                              "Pedido", "Fecha pedido", "Días cubiertos"], filas,
                        [2.5, 2.4, 1.9, 1.9, 1.9, 1.9, 2.3, 2.0]))
        h.append(Spacer(1, 4))
        h.append(Paragraph("Stock = inventario actual. Seguridad = stock de seguridad. Reorden = demanda esperada "
                           "durante el lead time + stock de seguridad; cuando el inventario lo alcanza, se pide la "
                           "cantidad indicada en Pedido.", st_["nota"]))

    ing = d.get("ingresos")
    if "Ingresos proyectados" in sec and ing:
        h.append(Spacer(1, 16))
        h.append(CondPageBreak(9 * cm))
        h.append(Paragraph("Ingresos proyectados", st_["h1"]))
        h.append(Paragraph(textos.get("ingresos") or explicar_ingresos(ing, d), st_["p"]))
        fl = sorted(ing["filas"], key=lambda f: -f["ingresos"])
        top = fl[:15]
        h.append(Image(grafico_barras([f["entidad"] for f in top], [f["ingresos"] for f in top],
                                      [f["color"] for f in top], E.clp_corto, formato_eje=E.clp_corto),
                       width=17 * cm, height=max(3.0, 0.55 * len(top) + 1.0) * cm))
        h.append(Spacer(1, 6))
        h.append(_tabla(st_, [_mayus(d["entidad"]), "Precio", "Ingresos esperados", "Rango probable"],
                        [[f["entidad"], E.clp(f["precio"]), E.clp(f["ingresos"]),
                          f"{E.clp_corto(f['ingresos_p10'])} a {E.clp_corto(f['ingresos_p90'])}"] for f in fl],
                        [5.0, 3.2, 4.0, 4.8]))

    esc = d.get("escenario")
    if "Último escenario" in sec and esc:
        h.append(Spacer(1, 16))
        h.append(CondPageBreak(9 * cm))
        h.append(Paragraph("Último escenario simulado", st_["h1"]))
        h.append(Paragraph(textos.get("escenario") or explicar_escenario(esc, d), st_["p"]))
        filas = []
        for f in esc["filas"]:
            b, c = f["demanda_base_evento"], f["demanda_esc_evento"]
            fila = [f["entidad"], E.num(b), E.num(c), E.pct((c / b - 1) * 100) if b else "—"]
            if esc.get("con_inventario"):
                fila += [E.num(f.get("perdida_extra_sin", 0)), E.num(f.get("perdida_extra_aj", 0))]
            filas.append(fila)
        enc = [_mayus(d["entidad"]), "Demanda sin evento", "Demanda con evento", "Cambio"]
        anchos = [4.4, 3.0, 3.0, 2.0]
        if esc.get("con_inventario"):
            enc += ["Sin atender si no reaccionas", "Sin atender ajustando"]
            anchos = [3.4, 2.4, 2.4, 1.8, 3.5, 3.5]
        h.append(_tabla(st_, enc, filas, anchos))

    h.append(Spacer(1, 14))
    h.append(Paragraph(f"Historial usado: {pd.Timestamp(d['hist_desde']):%d/%m/%Y} a "
                       f"{pd.Timestamp(d['hist_hasta']):%d/%m/%Y}, frecuencia {_t(d['frecuencia'])}. Los valores del "
                       "pronóstico son estimaciones; el rango probable indica la incertidumbre.", st_["nota"]))
    doc.build(h, onFirstPage=pie, onLaterPages=pie)
    return buf.getvalue()


def _mayus(t: str) -> str:
    t = str(t)
    return t if t.isupper() else t[:1].upper() + t[1:]

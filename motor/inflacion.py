"""Inflación para proyectar precios y costos hacia adelante.

Fuentes:
- FMI, World Economic Outlook (indicador PCPIPCH, variación anual del IPC promedio): un dato por año, con
  proyecciones para el año en curso y los siguientes. Reúne lo que publican los bancos centrales e institutos
  de estadística de cada país. API pública: https://www.imf.org/external/datamapper/api/
- Chile, IPC mensual del INE publicado por mindicador.cl (Banco Central / INE): variación acumulada de los
  últimos 12 meses.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

URL_FMI = ("https://www.imf.org/external/datamapper/api/v2/PCPIPCH/{iso}",
           "https://www.imf.org/external/datamapper/api/v1/PCPIPCH/{iso}")
URL_IPC_CHILE = "https://mindicador.cl/api/ipc"
TIEMPO_ESPERA = 8

# Latinoamérica primero; el resto por región
PAISES = {
    "CHL": "Chile", "ARG": "Argentina", "BOL": "Bolivia", "BRA": "Brasil", "COL": "Colombia",
    "CRI": "Costa Rica", "ECU": "Ecuador", "SLV": "El Salvador", "GTM": "Guatemala", "HND": "Honduras",
    "MEX": "México", "NIC": "Nicaragua", "PAN": "Panamá", "PRY": "Paraguay", "PER": "Perú",
    "DOM": "República Dominicana", "URY": "Uruguay", "VEN": "Venezuela",
    "USA": "Estados Unidos", "CAN": "Canadá",
    "DEU": "Alemania", "BEL": "Bélgica", "ESP": "España", "FRA": "Francia", "IRL": "Irlanda", "ITA": "Italia",
    "NLD": "Países Bajos", "POL": "Polonia", "PRT": "Portugal", "GBR": "Reino Unido", "SWE": "Suecia",
    "CHE": "Suiza", "TUR": "Turquía",
    "AUS": "Australia", "CHN": "China", "KOR": "Corea del Sur", "IND": "India", "IDN": "Indonesia",
    "JPN": "Japón", "NZL": "Nueva Zelanda", "SGP": "Singapur", "ZAF": "Sudáfrica", "ARE": "Emiratos Árabes Unidos",
}

META_POR_DEFECTO = 3.0   # % anual; meta del Banco Central de Chile, se usa si no hay conexión


def _get_json(url):
    import requests
    r = requests.get(url, timeout=TIEMPO_ESPERA, headers={"User-Agent": "motor-predictivo"})
    r.raise_for_status()
    return r.json()


def inflacion_fmi(iso: str) -> dict:
    """{'tasas': {año: % anual}, 'fuente': texto, 'proyeccion_desde': año}. Lanza excepción si no hay datos."""
    ultimo_error = None
    for url in URL_FMI:
        try:
            datos = _get_json(url.format(iso=iso))
            serie = datos["values"]["PCPIPCH"][iso]
            tasas = {int(a): float(v) for a, v in serie.items() if v is not None}
            if not tasas:
                raise ValueError("sin datos")
            meta = (datos.get("indicators") or {}).get("PCPIPCH", {})
            return dict(tasas=tasas, fuente=meta.get("source") or "FMI, World Economic Outlook",
                        proyeccion_desde=meta.get("projection-year"))
        except Exception as e:  # noqa: BLE001
            ultimo_error = e
    raise RuntimeError(f"FMI no respondió ({type(ultimo_error).__name__})")


def ipc_chile_12m() -> dict:
    """{'pct': variación del IPC en los últimos 12 meses, 'hasta': fecha del último dato}."""
    serie = _get_json(URL_IPC_CHILE)["serie"]
    df = pd.DataFrame(serie)
    df["fecha"] = pd.to_datetime(df["fecha"], utc=True).dt.tz_localize(None)
    df = df.sort_values("fecha").tail(12)
    if len(df) < 12:
        raise ValueError("menos de 12 meses de IPC")
    pct = (np.prod(1 + df["valor"].astype(float) / 100) - 1) * 100
    return dict(pct=float(pct), hasta=df["fecha"].iloc[-1])


def tasa_del_ano(tasas, ano: int) -> float:
    """% anual para un año: constante, o el del año pedido (el más cercano disponible si falta)."""
    if not isinstance(tasas, dict):
        return float(tasas)
    if ano in tasas:
        return tasas[ano]
    anos = sorted(tasas)
    return tasas[anos[-1]] if ano > anos[-1] else tasas[anos[0]]


def factores(fechas, base, tasas) -> np.ndarray:
    """Multiplicador de precios para cada fecha respecto de la fecha base, acumulando la inflación de cada año
    calendario en la fracción del año que corresponde."""
    base = pd.Timestamp(base)
    out = []
    for f in pd.to_datetime(pd.Series(fechas)):
        if f <= base:
            out.append(1.0)
            continue
        log_g, t = 0.0, base
        while t < f:
            fin_ano = pd.Timestamp(year=t.year + 1, month=1, day=1)
            hasta = min(f, fin_ano)
            frac = (hasta - t).days / 365.25
            log_g += frac * math.log1p(tasa_del_ano(tasas, t.year) / 100)
            t = hasta
        out.append(math.exp(log_g))
    return np.asarray(out, float)


def tasa_en_periodo(tasas, desde, hasta) -> float:
    """% anual equivalente entre dos fechas (para mostrar una sola cifra)."""
    desde, hasta = pd.Timestamp(desde), pd.Timestamp(hasta)
    anos = max((hasta - desde).days / 365.25, 1e-9)
    f = factores([hasta], desde, tasas)[0]
    return (f ** (1 / anos) - 1) * 100

"""Clima como variable externa del modelo (Open-Meteo: gratuito, sin clave).

- Historial: archivo de reanálisis (ERA5), con unos 5 días de atraso.
- Días recientes y próximos 16 días: API de pronóstico.
- Más allá: clima típico de esa fecha (promedio del mismo día del año en el historial).

Las variables entran al modelo como "temperatura" (°C, promedio diario) y "lluvia" (mm por día), llevadas a la
frecuencia del dataset.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import requests

URL_GEO = "https://geocoding-api.open-meteo.com/v1/search"
URL_HISTORIAL = "https://archive-api.open-meteo.com/v1/archive"
URL_PRONOSTICO = "https://api.open-meteo.com/v1/forecast"
DIARIAS = "temperature_2m_max,temperature_2m_min,precipitation_sum"
VARIABLES = ("temperatura", "lluvia")
ATRASO_HISTORIAL = 6        # días que tarda el archivo en tener datos definitivos
DIAS_PRONOSTICO = 16
TIMEOUT = 30


def buscar(nombre: str, n: int = 6) -> list[dict]:
    """Lugares que coinciden con el nombre: dict(nombre, lat, lon)."""
    r = requests.get(URL_GEO, params={"name": nombre, "count": n, "language": "es", "format": "json"}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for x in r.json().get("results") or []:
        partes = [x.get("name"), x.get("admin1"), x.get("country")]
        out.append({"nombre": ", ".join(p for p in partes if p), "lat": float(x["latitude"]),
                    "lon": float(x["longitude"])})
    return out


def _diario(js) -> pd.DataFrame:
    d = js.get("daily") or {}
    if not d.get("time"):
        return pd.DataFrame(columns=["fecha", *VARIABLES])
    tmax = np.array(d.get("temperature_2m_max"), dtype=float)
    tmin = np.array(d.get("temperature_2m_min"), dtype=float)
    return pd.DataFrame({"fecha": pd.to_datetime(d["time"]), "temperatura": (tmax + tmin) / 2,
                         "lluvia": np.array(d.get("precipitation_sum"), dtype=float)})


def historial(lat, lon, desde, hasta) -> pd.DataFrame:
    r = requests.get(URL_HISTORIAL, params={"latitude": lat, "longitude": lon, "start_date": f"{desde:%Y-%m-%d}",
                                            "end_date": f"{hasta:%Y-%m-%d}", "daily": DIARIAS, "timezone": "auto"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    return _diario(r.json())


def pronostico(lat, lon) -> pd.DataFrame:
    r = requests.get(URL_PRONOSTICO, params={"latitude": lat, "longitude": lon, "daily": DIARIAS, "timezone": "auto",
                                             "past_days": 10, "forecast_days": DIAS_PRONOSTICO}, timeout=TIMEOUT)
    r.raise_for_status()
    return _diario(r.json())


def serie_diaria(lat, lon, desde, hasta, hoy=None) -> pd.DataFrame:
    """Clima diario de desde a hasta (DataFrame indexado por fecha). El pasado es observado; lo que aún no ocurre
    viene del pronóstico (16 días) y, más allá, del clima típico de esa fecha."""
    desde, hasta = pd.Timestamp(desde).normalize(), pd.Timestamp(hasta).normalize()
    hoy = pd.Timestamp(hoy or pd.Timestamp.today()).normalize()
    fin_historial = min(hasta, hoy - pd.Timedelta(days=ATRASO_HISTORIAL))
    partes = []
    if fin_historial >= desde:
        partes.append(historial(lat, lon, desde, fin_historial))
    if hasta > fin_historial:
        try:
            partes.append(pronostico(lat, lon))
        except requests.RequestException:
            pass        # sin pronóstico se usa el clima típico
    obs = pd.concat(partes) if partes else pd.DataFrame(columns=["fecha", *VARIABLES])
    obs = obs.dropna(subset=["temperatura"]).drop_duplicates("fecha", keep="first").set_index("fecha").sort_index()
    idx = pd.date_range(desde, hasta, freq="D")
    serie = obs.reindex(idx)
    # clima típico: promedio del mismo día del año; si falta, el del mes; si falta, el promedio general
    if len(obs):
        dia = obs.groupby([obs.index.month, obs.index.day]).mean()
        mes = obs.groupby(obs.index.month).mean()
        for v in VARIABLES:
            faltan = serie[v].isna()
            if faltan.any():
                f = serie.index[faltan]
                tipico = [dia[v].get((m, d_), np.nan) for m, d_ in zip(f.month, f.day)]
                tipico = np.where(np.isnan(tipico), mes[v].reindex(f.month).to_numpy(), tipico)
                serie.loc[faltan, v] = np.where(np.isnan(tipico), obs[v].mean(), tipico)
    serie.index.name = "fecha"
    return serie.astype(float)


def por_periodo(diaria: pd.DataFrame, fechas, frecuencia: str) -> pd.DataFrame:
    """Clima de cada período del dataset: el día (D), los 7 días que terminan en la fecha (W), el mes o el
    trimestre que empieza en la fecha (M, Q)."""
    fechas = pd.DatetimeIndex(pd.to_datetime(fechas)).normalize()
    if frecuencia == "D":
        base = diaria
    elif frecuencia == "W":
        base = diaria.rolling(7, min_periods=1).mean()
    else:
        regla = "MS" if frecuencia == "M" else "QS"
        base = diaria.resample(regla).mean()
        fechas = fechas.to_period("M" if frecuencia == "M" else "Q").to_timestamp()
    out = base.reindex(fechas)
    out.index = pd.DatetimeIndex(pd.to_datetime(fechas))
    return out.ffill().bfill()


def agregar(df: pd.DataFrame, fechas_parseadas: pd.Series, diaria: pd.DataFrame, frecuencia: str) -> pd.DataFrame:
    """Copia del archivo con las columnas de clima de cada fila."""
    vals = por_periodo(diaria, fechas_parseadas.fillna(fechas_parseadas.min()), frecuencia)
    out = df.copy()
    for v in VARIABLES:
        out[v] = np.round(vals[v].to_numpy(), 1)
    return out


VENTANA_ANOMALIA = {"D": 28, "W": 8, "M": 3, "Q": 2}


def efecto(df: pd.DataFrame, frecuencia: str) -> pd.DataFrame:
    """Efecto medido del clima en la demanda de cada entidad, separado de la estacionalidad: se comparan las
    desviaciones de la demanda respecto de su nivel reciente con las desviaciones del clima respecto de lo
    normal para esa época. Devuelve entidad, variable, r (correlación), efecto_pct (% de demanda por °C o por mm)
    y relevante (correlación clara para el tamaño de la muestra)."""
    w = VENTANA_ANOMALIA.get(frecuencia, 8)
    filas = []
    for ent, g in df.sort_values("fecha").groupby("entidad"):
        y = g["objetivo"].astype(float)
        nivel = y.rolling(w, center=True, min_periods=max(2, w // 2)).mean()
        ya = y / nivel.replace(0, np.nan) - 1
        if frecuencia == "D":
            ya = ya - ya.groupby(g["fecha"].dt.dayofweek.to_numpy()).transform("mean")
        for v in VARIABLES:
            if v not in g:
                continue
            x = g[v].astype(float)
            xa = x - x.rolling(w, center=True, min_periods=max(2, w // 2)).mean()
            ok = ya.notna() & xa.notna()
            n = int(ok.sum())
            if n < 30 or xa[ok].std() == 0 or ya[ok].std() == 0:
                continue
            r = float(np.corrcoef(xa[ok], ya[ok])[0, 1])
            pend = float(np.cov(xa[ok], ya[ok])[0, 1] / xa[ok].var()) * 100
            filas.append({"entidad": ent, "variable": v, "r": r, "efecto_pct": pend, "n": n,
                          "relevante": abs(r) >= max(0.15, 2.5 / np.sqrt(n))})
    return pd.DataFrame(filas, columns=["entidad", "variable", "r", "efecto_pct", "n", "relevante"])


def usar_solo_si_influye(dp, frecuencia: str):
    """Deja en el modelo solo las variables de clima que muestran efecto claro en alguna entidad (si no influyen,
    agregarlas solo mete ruido). Guarda el efecto medido en dp.clima_efecto."""
    ef = efecto(dp.df, frecuencia)
    dp.clima_efecto = ef
    utiles = set(ef.loc[ef["relevante"], "variable"]) if len(ef) else set()
    dp.variables_modelo = [v for v in dp.variables_modelo if v not in VARIABLES or v in utiles]
    return dp

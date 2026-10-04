"""Motor de datos: carga, detección de roles, frecuencia, limpieza y diagnóstico.

Todo el resto del sitio trabaja sobre un `DatasetPreparado`, que tiene nombres de
columna canónicos (fecha, entidad, objetivo, precio, ...), una sola fila por
entidad y período, y una frecuencia regular.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- roles

ROLES = {
    "fecha": dict(etiqueta="Fecha", obligatorio=True,
                  claves=["fecha", "date", "dia", "day", "periodo", "period", "timestamp", "datetime", "semana", "week", "mes", "month", "ds"]),
    "entidad": dict(etiqueta="Entidad (SKU, tienda, producto)", obligatorio=False,
                    claves=["sku", "producto", "product", "item", "articulo", "tienda", "store", "sucursal", "local",
                            "codigo", "code", "id", "categoria", "category", "cliente", "customer", "serie", "unique_id"]),
    "objetivo": dict(etiqueta="Variable a pronosticar", obligatorio=True,
                     claves=["demanda", "demand", "venta", "ventas", "sales", "unidades", "units", "cantidad", "qty",
                             "quantity", "volumen", "volume", "pedidos", "orders", "consumo", "y", "target"]),
    "precio": dict(etiqueta="Precio", obligatorio=False, claves=["precio", "price", "pvp", "tarifa"]),
    "promocion": dict(etiqueta="Promoción (0/1)", obligatorio=False,
                      claves=["promo", "promocion", "promotion", "oferta", "descuento", "discount", "onpromotion", "campana"]),
    "lead_time": dict(etiqueta="Lead time (días)", obligatorio=False,
                      claves=["lead_time", "leadtime", "lead", "plazo_entrega", "tiempo_entrega", "plazo", "reposicion"]),
    "inventario": dict(etiqueta="Inventario disponible", obligatorio=False,
                       claves=["inventario", "stock", "existencia", "existencias", "inventory", "on_hand", "saldo"]),
    "quiebre": dict(etiqueta="Quiebre de stock (0/1)", obligatorio=False,
                    claves=["quiebre", "stockout", "rotura", "faltante", "sin_stock", "out_of_stock"]),
    "costo_unitario": dict(etiqueta="Costo unitario", obligatorio=False,
                           claves=["costo_unitario", "costo", "cost", "unit_cost", "precio_compra", "precio_unitario_primario"]),
}
_NO_ENTIDAD = {"comentario", "comentarios", "comment", "comments", "observacion", "observaciones", "nota", "notas",
               "estado", "status", "moneda", "currency", "unidad", "unidad_medida"}
ROLES_MODELO = ("precio", "promocion")  # roles que, además, entran al modelo como exógenas
ROLES_POLITICA = ("lead_time", "inventario", "quiebre", "costo_unitario")  # solo para indicadores y política


def _normalizar(nombre: str) -> str:
    s = unicodedata.normalize("NFKD", str(nombre)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return s


DERIVADAS_FECHA = {"ano", "anio", "year", "mes", "month", "dia", "day", "dia_semana", "weekday", "dayofweek",
                   "day_of_week", "semana", "week", "trimestre", "quarter", "dia_mes", "dia_ano", "dia_anio",
                   "semana_ano", "semana_anio", "numero_semana", "fin_de_semana", "weekend"}


def es_derivada_de_fecha(nombre: str) -> bool:
    """Columnas que solo repiten información de la fecha (año, mes, día de semana...).
    El modelo ya usa el calendario, así que no aportan y confunden al usuario."""
    return _normalizar(nombre) in DERIVADAS_FECHA


_PALABRAS = {"dia": "día", "dias": "días", "indice": "índice", "promocion": "promoción", "estres": "estrés",
             "critico": "crítico", "critica": "crítica", "ano": "año", "anio": "año", "categoria": "categoría",
             "region": "región", "numero": "número", "cantidad": "cantidad", "periodo": "período",
             "descripcion": "descripción", "codigo": "código", "ubicacion": "ubicación", "campana": "campaña",
             "estacion": "estación", "reposicion": "reposición", "produccion": "producción",
             "distribucion": "distribución", "transaccion": "transacción", "transacciones": "transacciones"}
_SIGLAS = {"sku", "id", "clp", "usd", "eur", "kg", "ipc", "iva", "uf", "cd", "ss", "rop"}
_UNIDADES = {"unidades": "unidades", "unid": "unidades", "u": "unidades", "dias": "días", "clp": "CLP",
             "usd": "USD", "eur": "EUR", "kg": "kg", "pct": "%", "porcentaje": "%", "ton": "toneladas",
             "litros": "litros", "lt": "litros", "horas": "horas", "semanas": "semanas", "meses": "meses", "uf": "UF"}


def nombre_legible(columna: str) -> str:
    """demanda_unidades -> Demanda (unidades); lead_time_dias -> Lead time (días); precio_clp -> Precio (CLP)."""
    s = str(columna).strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-\s\.]+", s) or (" " in s and s[0].isupper()):
        return s  # ya viene escrito para personas (con tildes, espacios y mayúsculas)
    partes = [p for p in re.split(r"[_\-\s\.]+", re.sub(r"(?<=[a-z])(?=[A-Z])", "_", s)) if p]
    if not partes:
        return s
    partes = [p.lower() for p in partes]
    unidad = None
    if len(partes) > 1 and partes[-1] in _UNIDADES:
        unidad = _UNIDADES[partes.pop()]
    palabras = [p.upper() if p in _SIGLAS else _PALABRAS.get(p, p) for p in partes]
    if palabras[0] == palabras[0].lower():
        palabras[0] = palabras[0][0].upper() + palabras[0][1:]
    texto = " ".join(palabras)
    return f"{texto} ({unidad})" if unidad else texto


def _score_nombre(col: str, claves: list[str]) -> float:
    n = _normalizar(col)
    tokens = set(n.split("_"))
    mejor = 0.0
    for c in claves:
        if n == c:
            return 1.0
        if c in tokens or ("_" in c and c in n):
            mejor = max(mejor, 0.85)
        elif len(c) >= 4 and c in n:
            mejor = max(mejor, 0.6)
    return mejor


# ---------------------------------------------------------------- carga

class ArchivoInvalido(ValueError):
    """Error de lectura con un mensaje pensado para el usuario."""


def _decodificar(contenido: bytes) -> str:
    try:
        return contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        return contenido.decode("latin-1")


def _fila_encabezado(crudo: pd.DataFrame) -> int:
    """Índice de la fila que parece el encabezado real (para archivos con títulos arriba)."""
    llenas = crudo.notna().sum(axis=1)
    ancho = llenas.max()
    for i in range(min(len(crudo), 20)):
        fila = crudo.iloc[i]
        textos = [v for v in fila.dropna() if isinstance(v, str)]
        if (llenas.iloc[i] >= max(2, ancho * 0.8) and len(textos) == llenas.iloc[i]
                and not any(re.fullmatch(r"-?[\d.,]+", v.strip()) for v in textos)):
            return i
    return 0


_MILES_PUNTO = re.compile(r"^-?\$?\s?\d{1,3}(\.\d{3})+(,\d+)?$")
_MILES_COMA = re.compile(r"^-?\$?\s?\d{1,3}(,\d{3})+(\.\d+)?$")


def _numeros_con_formato(df: pd.DataFrame) -> pd.DataFrame:
    """Columnas de texto que en realidad son números con separador de miles o signo $ (1.234.567 / $1,500)."""
    for c in df.columns:
        if df[c].dtype != object:
            continue
        t = df[c].dropna().astype(str).str.strip()
        if t.empty:
            continue
        if t.str.match(_MILES_PUNTO).mean() >= 0.6 and t.str.match(r"^-?\$?\s?[\d.]+(,\d+)?$").mean() >= 0.95:
            limpio = df[c].astype(str).str.replace(r"[$\s.]", "", regex=True).str.replace(",", ".")
        elif t.str.match(_MILES_COMA).mean() >= 0.6 and t.str.match(r"^-?\$?\s?[\d,]+(\.\d+)?$").mean() >= 0.95:
            limpio = df[c].astype(str).str.replace(r"[$\s,]", "", regex=True)
        elif t.str.match(r"^-?\$\s?\d+([.,]\d+)?$").mean() >= 0.95:
            limpio = df[c].astype(str).str.replace(r"[$\s]", "", regex=True).str.replace(",", ".")
        else:
            continue
        df[c] = pd.to_numeric(limpio.where(df[c].notna()), errors="coerce")
    return df


def leer_archivo(nombre: str, contenido: bytes) -> pd.DataFrame:
    """Lee CSV (detecta separador , ; tab | y decimales con coma) o Excel. Salta filas de título sobre el
    encabezado y convierte números escritos con separador de miles."""
    if not contenido or not contenido.strip():
        raise ArchivoInvalido("El archivo está vacío.")
    ext = nombre.lower().rsplit(".", 1)[-1]
    try:
        if ext in ("xlsx", "xls", "xlsm"):
            crudo = pd.read_excel(io.BytesIO(contenido), header=None)
            h = _fila_encabezado(crudo)
            df = pd.read_excel(io.BytesIO(contenido), header=h)
        else:
            texto = _decodificar(contenido)
            muestra = texto[:20000]
            sep = max([",", ";", "\t", "|"], key=lambda s: muestra.count(s))
            decimal = "," if sep == ";" and re.search(r"\d,\d", muestra) else "."
            lineas = list(csv.reader(io.StringIO("\n".join(texto.splitlines()[:25])), delimiter=sep))
            crudo = pd.DataFrame([[v if v.strip() else None for v in fila] for fila in lineas])
            h = _fila_encabezado(crudo) if len(crudo) else 0
            df = pd.read_csv(io.StringIO(texto), sep=sep, decimal=decimal, skiprows=h,
                             on_bad_lines="skip" if h else "error")
    except ArchivoInvalido:
        raise
    except pd.errors.EmptyDataError:
        raise ArchivoInvalido("El archivo no tiene datos.")
    except Exception as e:  # noqa: BLE001
        raise ArchivoInvalido(f"El formato del archivo no se pudo interpretar ({type(e).__name__}).") from e
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    if df.empty or len(df.columns) == 0:
        raise ArchivoInvalido("El archivo no tiene datos.")
    return _numeros_con_formato(df.reset_index(drop=True))


# ---------------------------------------------------------------- fechas

def _regularidad(fechas: pd.Series) -> float:
    """Fracción de saltos entre fechas únicas que caen en el salto típico (día, semana, mes, trimestre)."""
    u = pd.Series(pd.to_datetime(fechas.dropna().unique())).sort_values()
    d = u.diff().dt.days.dropna()
    if d.empty:
        return 0.0
    tramo = pd.cut(d, [0, 1.5, 8, 32, 93, 1e9], labels=False)
    return float(tramo.value_counts(normalize=True).iloc[0])


_SEMANA_ISO = re.compile(r"^\d{4}-?W\d{1,2}$", re.IGNORECASE)


def parsear_fechas(serie: pd.Series) -> tuple[pd.Series, str]:
    """Convierte a fecha probando formato ISO, día/mes y mes/día. Devuelve (fechas, nota)."""
    if pd.api.types.is_datetime64_any_dtype(serie):
        return serie, "ya venía como fecha"
    if pd.api.types.is_numeric_dtype(serie):
        # años sueltos (2019, 2020...) o yyyymmdd
        v = serie.dropna()
        if len(v) and v.between(1900, 2100).all():
            return pd.to_datetime(serie.astype("Int64").astype(str), format="%Y", errors="coerce"), "años"
        if len(v) and v.between(19000101, 21001231).all():
            return pd.to_datetime(serie.astype("Int64").astype(str), format="%Y%m%d", errors="coerce"), "formato AAAAMMDD"
        return pd.Series(pd.NaT, index=serie.index), "numérica, no es fecha"

    texto = serie.astype(str).str.strip()
    if texto.str.match(_SEMANA_ISO).mean() > 0.9:
        t = texto.str.upper().str.replace(r"^(\d{4})-?W(\d{1,2})$", lambda m: f"{m[1]}-W{int(m[2]):02d}-1", regex=True)
        return pd.to_datetime(t, format="%G-W%V-%u", errors="coerce"), "semana ISO (AAAA-Wnn)"
    candidatos = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for nota, kwargs in [
            ("formato ISO (AAAA-MM-DD)", dict(format="ISO8601")),
            ("día/mes/año", dict(dayfirst=True, format="mixed")),
            ("mes/día/año", dict(dayfirst=False, format="mixed")),
        ]:
            try:
                f = pd.to_datetime(texto, errors="coerce", **kwargs)
            except (ValueError, TypeError):
                continue
            candidatos.append((f.notna().mean(), nota, f))
    if not candidatos:
        return pd.Series(pd.NaT, index=serie.index), "no se pudo interpretar"

    # Entre día/mes y mes/día: si algún primer número es > 12, manda ese orden
    partes = texto.str.extract(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.]\d{2,4}")
    if partes.notna().all(axis=1).mean() > 0.5:
        a = pd.to_numeric(partes[0], errors="coerce")
        b = pd.to_numeric(partes[1], errors="coerce")
        if (a > 12).any():
            preferido = "día/mes/año"
        elif (b > 12).any():
            preferido = "mes/día/año"
        else:   # ambiguo: gana el orden que deja las fechas más regulares
            regular = {nota: _regularidad(f) for _, nota, f in candidatos if nota != "formato ISO (AAAA-MM-DD)"}
            preferido = "mes/día/año" if regular.get("mes/día/año", 0) > regular.get("día/mes/año", 0) + 0.05 \
                else "día/mes/año"
        for tasa, nota, f in candidatos:
            if nota == preferido and tasa >= 0.9:
                return f, nota
    tasa, nota, f = max(candidatos, key=lambda c: c[0])
    return f, nota


# ---------------------------------------------------------------- detección de roles

@dataclass
class Deteccion:
    roles: dict                    # rol -> columna original (o None)
    confianza: dict                # rol -> 0..1
    exogenas: list                 # columnas originales sugeridas como exógenas extra
    advertencias: list = field(default_factory=list)


_VERDADERO = {"1", "si", "sí", "s", "yes", "y", "true", "verdadero", "x"}
_FALSO = {"0", "no", "n", "false", "falso", ""}


def a_numero(s: pd.Series) -> pd.Series:
    """Convierte a número aceptando booleanos y textos sí/no, true/false."""
    if pd.api.types.is_bool_dtype(s):
        return s.astype(float)
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float)
    x = pd.to_numeric(s, errors="coerce")
    if x.notna().mean() >= 0.9:
        return x
    t = s.astype(str).str.strip().str.lower()
    t = t.where(s.notna(), None)
    vistos = set(t.dropna().unique())
    if vistos and vistos <= (_VERDADERO | _FALSO):
        return t.map(lambda v: np.nan if v is None else (1.0 if v in _VERDADERO else 0.0))
    return x


def _es_binaria(s: pd.Series) -> bool:
    v = a_numero(s).dropna().unique()
    return len(v) > 0 and set(np.round(v.astype(float), 6)).issubset({0.0, 1.0})


def _score_entidad_estructura(s: pd.Series, n: int) -> float:
    u = s.nunique(dropna=True)
    if u < 2 or u > max(50_000, n // 3):
        return 0.0
    rep = n / u
    if rep < 3:
        return 0.0
    score = min(1.0, rep / 50)
    if pd.api.types.is_float_dtype(s):
        score *= 0.1
    elif pd.api.types.is_integer_dtype(s):
        score *= 0.5
    return score


def detectar_roles(df: pd.DataFrame) -> Deteccion:
    cols = list(df.columns)
    n = len(df)
    roles, conf, adv = {}, {}, []
    usadas = set()

    # 1) fecha: nombre + qué fracción se puede parsear
    mejor, mejor_s = None, 0.0
    for c in cols:
        f, _ = parsear_fechas(df[c].head(2000))
        tasa = f.notna().mean()
        if tasa < 0.8:
            continue
        s = 0.6 * tasa + 0.4 * _score_nombre(c, ROLES["fecha"]["claves"])
        if pd.api.types.is_numeric_dtype(df[c]) and _score_nombre(c, ROLES["fecha"]["claves"]) == 0:
            s *= 0.3
        if s > mejor_s:
            mejor, mejor_s = c, s
    roles["fecha"], conf["fecha"] = mejor, round(mejor_s, 2)
    if mejor is None:
        adv.append("No se encontró una columna de fecha. Elígela manualmente.")
    usadas.add(mejor)

    numericas = [c for c in cols if c not in usadas and (pd.api.types.is_numeric_dtype(df[c]) or _es_binaria(df[c]))]

    # 2) entidad: nombre + estructura (se repite, pocas categorías y separa las fechas repetidas)
    fechas_det = parsear_fechas(df[roles["fecha"]])[0].dt.normalize() if roles["fecha"] else None
    frac_fecha_rep = float(fechas_det.duplicated().mean()) if fechas_det is not None else 1.0
    mejor, mejor_s = None, 0.0
    for c in cols:
        if c in usadas or _es_binaria(df[c]) or _normalizar(c) in _NO_ENTIDAD:
            continue
        nombre_ent = _score_nombre(c, ROLES["entidad"]["claves"])
        if any(_score_nombre(c, ROLES[r]["claves"]) >= max(0.85, nombre_ent + 1e-9)
               for r in ROLES if r not in ("entidad", "fecha")):
            continue
        est = _score_entidad_estructura(df[c], n)
        if est == 0:
            continue
        if fechas_det is not None:
            if frac_fecha_rep < 0.02:          # una fila por fecha: es una sola serie
                continue
            pares = pd.DataFrame({"e": df[c].values, "f": fechas_det.values})
            resuelve = 1 - float(pares.duplicated().mean()) / frac_fecha_rep
            if resuelve < 0.3:
                # datos transaccionales: la entidad no elimina las fechas repetidas, pero las reparte
                reparte = len(pares.drop_duplicates()) / max(fechas_det.nunique(), 1)
                if not (nombre_ent >= 0.6 and reparte >= 1.5):
                    continue
                resuelve = 0.5
            est = max(est, 0.6) * resuelve if resuelve >= 0.9 else est * resuelve
        s = 0.5 * nombre_ent + 0.5 * est
        if s > mejor_s:
            mejor, mejor_s = c, s
    if mejor is not None and mejor_s >= 0.2:
        roles["entidad"], conf["entidad"] = mejor, round(mejor_s, 2)
        usadas.add(mejor)
    else:
        roles["entidad"], conf["entidad"] = None, 0.0

    # 3) roles opcionales por nombre + validación de tipo
    def validar(rol, s):
        x = a_numero(s)
        if x.notna().mean() < 0.9:
            return False
        if rol in ("promocion", "quiebre"):
            return _es_binaria(s)
        if rol == "lead_time":
            return x.min() >= 0 and x.max() <= 730
        if rol in ("precio", "inventario", "costo_unitario"):
            return x.min() >= 0 and not _es_binaria(s)
        return True

    for rol in ("promocion", "quiebre", "lead_time", "inventario", "costo_unitario", "precio"):
        mejor, mejor_s = None, 0.0
        for c in numericas:
            if c in usadas:
                continue
            s = _score_nombre(c, ROLES[rol]["claves"])
            if s >= 0.6 and validar(rol, df[c]) and s > mejor_s:
                mejor, mejor_s = c, s
        roles[rol], conf[rol] = mejor, round(mejor_s, 2)
        if mejor:
            usadas.add(mejor)

    # 4) objetivo: nombre; si no hay, la numérica continua con más variación
    mejor, mejor_s = None, 0.0
    for c in numericas:
        if c in usadas:
            continue
        s = _score_nombre(c, ROLES["objetivo"]["claves"])
        if _es_binaria(df[c]) and s < 0.85:
            continue
        if s > mejor_s:
            mejor, mejor_s = c, s
    if mejor is None:
        restantes = [c for c in numericas if c not in usadas and not _es_binaria(df[c]) and df[c].nunique() > 10]
        if restantes:
            mejor = max(restantes, key=lambda c: df[c].std() / (abs(df[c].mean()) + 1e-9))
            mejor_s = 0.3
            adv.append(f"La variable a pronosticar se eligió por descarte ('{mejor}'). Revísala.")
    roles["objetivo"], conf["objetivo"] = mejor, round(mejor_s, 2)
    if mejor is None:
        adv.append("No se encontró ninguna columna numérica que se pueda pronosticar.")
    usadas.add(mejor)

    # 5) exógenas extra: numéricas que no tomaron rol y que varían en el tiempo
    exogenas = [c for c in numericas if c not in usadas and pd.api.types.is_numeric_dtype(df[c])
                and not pd.api.types.is_bool_dtype(df[c]) and df[c].nunique() > 1]
    # descartar columnas derivadas de la fecha (año, mes, día de semana)
    exogenas = [c for c in exogenas if not es_derivada_de_fecha(c)]
    # descartar las que no cambian en el tiempo dentro de ninguna entidad (atributos fijos del producto)
    if roles.get("entidad"):
        exogenas = [c for c in exogenas if df.groupby(roles["entidad"])[c].nunique().max() > 1]

    return Deteccion(roles=roles, confianza=conf, exogenas=exogenas, advertencias=adv)


# ---------------------------------------------------------------- frecuencia

FRECUENCIAS = {
    "D": dict(nombre="diaria", unidad="día", unidad_pl="días", periodo_estacional=7, pandas="D", dias=1),
    "W": dict(nombre="semanal", unidad="semana", unidad_pl="semanas", periodo_estacional=52, pandas="W-MON", dias=7),
    "M": dict(nombre="mensual", unidad="mes", unidad_pl="meses", periodo_estacional=12, pandas="MS", dias=30.4),
    "Q": dict(nombre="trimestral", unidad="trimestre", unidad_pl="trimestres", periodo_estacional=4, pandas="QS", dias=91.3),
}


def detectar_frecuencia(fechas: pd.Series, entidades: pd.Series | None) -> tuple[str, dict]:
    """Devuelve (código, info). info trae la mediana de días entre registros y si hay
    varias filas por entidad y fecha (datos transaccionales que hay que agregar)."""
    d = pd.DataFrame({"f": fechas, "e": entidades if entidades is not None else "_"}).dropna()
    duplicadas = d.duplicated(["e", "f"]).mean()
    unicas = d.drop_duplicates(["e", "f"]).sort_values(["e", "f"])
    difs = unicas.groupby("e")["f"].diff().dt.days.dropna()
    mediana = float(difs.median()) if len(difs) else np.nan
    if np.isnan(mediana):
        codigo = "D"
    elif mediana <= 1.5:
        codigo = "D"
    elif mediana <= 8:
        codigo = "W"
    elif mediana <= 32:
        codigo = "M"
    else:
        codigo = "Q"
    return codigo, dict(mediana_dias=mediana, frac_duplicadas=float(duplicadas))


# ---------------------------------------------------------------- preparación

@dataclass
class Configuracion:
    roles: dict              # rol -> columna original o None
    exogenas: list           # columnas originales extra que entran al modelo
    frecuencia: str          # D / W / M / Q
    relleno_objetivo: str = "interpolar"   # "interpolar" o "cero"
    negativos_a_cero: bool = True
    suavizar_picos: bool = False           # el modelo entrena con los picos aislados reemplazados por su valor típico


@dataclass
class DatasetPreparado:
    df: pd.DataFrame          # columnas: fecha, entidad, objetivo, [roles opcionales], [exógenas]
    config: Configuracion
    etiquetas: dict           # columna canónica -> nombre original (para mostrar)
    variables_modelo: list    # objetivo + exógenas que entran al modelo, en orden
    reporte: list             # acciones de limpieza realizadas (texto)
    n_filas_original: int
    picos: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["entidad", "fecha", "valor", "tipico"]))

    @property
    def entidades(self) -> list:
        return sorted(self.df["entidad"].unique().tolist())

    @property
    def freq_info(self) -> dict:
        return FRECUENCIAS[self.config.frecuencia]

    def tiene(self, rol: str) -> bool:
        return rol in self.df.columns


AGREGACION = {
    "objetivo": "sum", "precio": "mean", "promocion": "max", "lead_time": "mean",
    "inventario": "last", "quiebre": "max", "costo_unitario": "mean",
}


def alinear_fechas(fechas: pd.Series, frecuencia: str) -> pd.Series:
    """Lleva cada fecha al inicio de su período (día, semana, mes o trimestre)."""
    fq = FRECUENCIAS[frecuencia]["pandas"]
    if frecuencia == "D":
        return fechas.dt.normalize()
    return fechas.dt.to_period(fq.split("-")[0] if frecuencia == "W" else fq[0]).dt.start_time


def preparar(df_original: pd.DataFrame, config: Configuracion) -> DatasetPreparado:
    roles = config.roles
    if not roles.get("fecha") or not roles.get("objetivo"):
        raise ValueError("Faltan columnas obligatorias: se necesita una fecha y una variable a pronosticar.")

    reporte = []
    n0 = len(df_original)
    renombre = {orig: rol for rol, orig in roles.items() if orig}
    exog = [c for c in config.exogenas if c not in renombre]
    etiquetas = {rol: nombre_legible(orig) for rol, orig in roles.items() if orig}
    etiquetas.update({c: nombre_legible(c) for c in exog})

    df = df_original[list(renombre) + exog].rename(columns=renombre).copy()
    if "entidad" not in df.columns:
        df["entidad"] = "Serie única"
        etiquetas["entidad"] = "(una sola serie)"
    df["entidad"] = df["entidad"].astype(str).str.strip()

    # fechas
    df["fecha"], nota = parsear_fechas(df["fecha"])
    malas = df["fecha"].isna().sum()
    if malas:
        reporte.append(f"Se descartaron {malas:,} filas con fecha ilegible.".replace(",", "."))
        df = df[df["fecha"].notna()]
    reporte.append(f"Fechas interpretadas como {nota}.")

    # numéricos
    numericas = [c for c in df.columns if c not in ("fecha", "entidad")]
    for c in numericas:
        df[c] = a_numero(df[c])
    sin_obj = df["objetivo"].isna().sum()
    if sin_obj:
        reporte.append(f"{sin_obj:,} filas sin valor en la variable a pronosticar se tratarán como faltantes.".replace(",", "."))

    # alinear fecha al inicio del período
    fq = FRECUENCIAS[config.frecuencia]["pandas"]
    df["fecha"] = alinear_fechas(df["fecha"], config.frecuencia)

    # agregar duplicados (datos transaccionales o frecuencia más fina)
    n_antes = len(df)
    agg = {c: AGREGACION.get(c, "mean") for c in numericas}
    df = df.sort_values(["entidad", "fecha"]).groupby(["entidad", "fecha"], as_index=False).agg(agg)
    if len(df) < n_antes:
        reporte.append(
            f"Se agruparon {n_antes - len(df):,} filas repetidas por entidad y {FRECUENCIAS[config.frecuencia]['unidad']} "
            f"(la demanda se sumó; precio y costo se promediaron).".replace(",", "."))

    # regularizar: una fila por período, sin huecos
    partes, huecos_total = [], 0
    for ent, g in df.groupby("entidad", sort=True):
        rango = pd.date_range(g["fecha"].min(), g["fecha"].max(), freq=fq)
        g = g.set_index("fecha").reindex(rango)
        g.index.name = "fecha"
        huecos = int(g["objetivo"].isna().sum())
        huecos_total += huecos
        if config.relleno_objetivo == "cero":
            g["objetivo"] = g["objetivo"].fillna(0)
        else:
            g["objetivo"] = g["objetivo"].interpolate(limit_direction="both")
        for c in numericas:
            if c == "objetivo":
                continue
            if c in ("promocion", "quiebre"):
                g[c] = g[c].fillna(0)
            else:
                g[c] = g[c].ffill().bfill()
        g["entidad"] = ent
        partes.append(g.reset_index())
    df = pd.concat(partes, ignore_index=True)
    if huecos_total:
        metodo = "con cero (sin ventas ese período)" if config.relleno_objetivo == "cero" else "por interpolación"
        reporte.append(f"Se completaron {huecos_total:,} períodos faltantes {metodo}.".replace(",", "."))

    # negativos
    neg = int((df["objetivo"] < 0).sum())
    if neg and config.negativos_a_cero:
        df["objetivo"] = df["objetivo"].clip(lower=0)
        reporte.append(f"{neg:,} valores negativos (devoluciones) se llevaron a cero.".replace(",", "."))

    # exógenas constantes no aportan al modelo
    variables_modelo = ["objetivo"]
    for c in [r for r in ROLES_MODELO if r in df.columns] + exog:
        if df.groupby("entidad")[c].nunique().max() > 1:
            variables_modelo.append(c)
        else:
            reporte.append(f"'{etiquetas.get(c, c)}' no varía en el tiempo dentro de ninguna entidad y no se usará en el modelo.")
    columnas = ["fecha", "entidad"] + [c for c in df.columns if c not in ("fecha", "entidad")]
    df = df[columnas].sort_values(["entidad", "fecha"]).reset_index(drop=True)

    return DatasetPreparado(df=df, config=config, etiquetas=etiquetas, variables_modelo=variables_modelo,
                            reporte=reporte, n_filas_original=n0, picos=detectar_picos(df, config.frecuencia))


# ---------------------------------------------------------------- picos aislados

VENTANA_PICOS = {"D": 15, "W": 9, "M": 7, "Q": 5}   # largo de la mediana móvil centrada
DESFASE_ANUAL = {"D": 364, "W": 52, "M": 12, "Q": 4}


def detectar_picos(df: pd.DataFrame, frecuencia: str) -> pd.DataFrame:
    """Picos aislados de demanda por entidad.

    Un período es pico si supera la mediana móvil centrada en más de 5 desviaciones robustas (MAD) y además
    triplica esa mediana. No se marcan: períodos con promoción, picos que se repiten en la misma época del año
    (estacionalidad), series intermitentes (30% o más de ceros) ni entidades donde los picos son frecuentes
    (más de 3% de los períodos), porque ahí son parte del comportamiento normal.
    """
    cols = ["entidad", "fecha", "valor", "tipico"]
    w, anual = VENTANA_PICOS[frecuencia], DESFASE_ANUAL[frecuencia]
    filas = []
    for e, g in df.groupby("entidad", sort=False):
        y = g["objetivo"].to_numpy(float)
        n = len(y)
        if n < w or (y <= 0).mean() >= 0.30:
            continue
        serie = pd.Series(y)
        med = serie.rolling(w, center=True, min_periods=w // 2 + 1).median().to_numpy()
        mad = (serie - med).abs().rolling(w, center=True, min_periods=w // 2 + 1).median().to_numpy()
        escala = np.maximum(1.4826 * mad, 0.1 * np.abs(med))
        alto = (y > med + 5 * escala) & (y > 3 * med) & (med > 0)
        if "promocion" in g.columns:
            alto &= ~(g["promocion"].fillna(0).to_numpy() > 0)
        idx = []
        for i in np.where(alto)[0]:
            otros = [j for j in (i - anual, i + anual) if 0 <= j < n]
            if any(y[j] > 2 * med[j] for j in otros if med[j] > 0):
                continue
            idx.append(i)
        if not idx or len(idx) > max(2, 0.03 * n):
            continue
        for i in idx:
            filas.append(dict(entidad=e, fecha=g["fecha"].iloc[i], valor=float(y[i]), tipico=float(med[i])))
    return pd.DataFrame(filas, columns=cols)


# ---------------------------------------------------------------- diagnóstico

def resumen_por_entidad(dp: DatasetPreparado) -> pd.DataFrame:
    g = dp.df.groupby("entidad")
    r = pd.DataFrame({
        "registros": g.size(),
        "desde": g["fecha"].min(),
        "hasta": g["fecha"].max(),
        "promedio": g["objetivo"].mean(),
        "cv": g["objetivo"].std() / g["objetivo"].mean().replace(0, np.nan),
        "pct_ceros": g["objetivo"].apply(lambda s: (s == 0).mean() * 100),
    }).reset_index()
    return r

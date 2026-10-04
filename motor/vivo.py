"""Datos en vivo: filas nuevas que llegan después de entrenar (API, link a una planilla o formulario).

- Clave de integración: una por pronóstico guardado. Se muestra una sola vez; se guarda solo su hash.
- Filas en vivo: (fecha, entidad, objetivo, inventario, precio). Una por fecha y entidad; la última gana.
- Mezcla: las filas con ventas reemplazan en el archivo original los períodos que traen y agregan los nuevos.
  El inventario no necesita reentrenar: se usa directo en Decisiones.

Sin pandas al importar: la API (Render) usa solo validación y almacenamiento.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import sqlite3
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import requests

PREFIJO_CLAVE = "mp_"
MAX_FILAS_POR_ENVIO = 5000
MAX_BYTES_LINK = 15 * 1024 * 1024
CAMPOS_VALOR = ("objetivo", "inventario", "precio")
COLUMNAS = ("fecha", "entidad") + CAMPOS_VALOR

# nombres aceptados en el JSON / en la planilla (sin tildes ni mayúsculas)
SINONIMOS = {
    "fecha": ("fecha", "date", "dia", "periodo", "semana", "mes"),
    "entidad": ("entidad", "sku", "producto", "item", "articulo", "codigo", "serie", "id"),
    "objetivo": ("ventas", "venta", "objetivo", "demanda", "cantidad", "unidades", "sales", "qty"),
    "inventario": ("inventario", "stock", "existencias", "on_hand"),
    "precio": ("precio", "price", "precio_unitario"),
}

SQL_SUPABASE = """
-- Datos en vivo: claves de integración y filas que llegan después de entrenar
create table if not exists public.integraciones (
    registro_id  uuid primary key references public.pronosticos(id) on delete cascade,
    usuario      text not null,
    clave_hash   text not null unique,       -- sha256 de la clave; la clave no se guarda
    prefijo      text not null,              -- primeros caracteres, para reconocerla
    activa       boolean not null default true,
    creado       timestamptz not null default now(),
    ultimo_uso   timestamptz
);

create table if not exists public.datos_vivo (
    id           bigint generated always as identity primary key,
    registro_id  uuid not null references public.pronosticos(id) on delete cascade,
    usuario      text not null,
    fecha        date not null,
    entidad      text not null default '',
    objetivo     double precision,
    inventario   double precision,
    precio       double precision,
    origen       text not null default 'api',  -- api / formulario
    recibido     timestamptz not null default now(),
    unique (registro_id, fecha, entidad)
);
create index if not exists datos_vivo_registro_idx on public.datos_vivo (registro_id, fecha);

alter table public.integraciones enable row level security;
alter table public.datos_vivo enable row level security;
grant select, insert, update, delete on table public.integraciones to service_role;
grant select, insert, update, delete on table public.datos_vivo to service_role;
grant usage, select on all sequences in schema public to service_role;
revoke all on table public.integraciones from anon, authenticated;
revoke all on table public.datos_vivo from anon, authenticated;
""".strip()


def _ahora():
    return datetime.now(timezone.utc).isoformat()


def hash_clave(clave: str) -> str:
    return hashlib.sha256(clave.strip().encode()).hexdigest()


def nueva_clave() -> str:
    return PREFIJO_CLAVE + secrets.token_urlsafe(30)


def _norm(nombre) -> str:
    t = str(nombre).strip().lower()
    for a, b in zip("áéíóúñ", "aeioun"):
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "_", t).strip("_")


# ---------------------------------------------------------------- validación

class FilasInvalidas(ValueError):
    def __init__(self, errores):
        self.errores = errores
        super().__init__(f"{len(errores)} fila(s) con problemas")


def _numero(v, campo, minimo=None):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        raise ValueError(f"'{campo}' debe ser un número")
    try:
        x = float(str(v).replace(",", ".")) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        raise ValueError(f"'{campo}' debe ser un número") from None
    if not math.isfinite(x):
        raise ValueError(f"'{campo}' debe ser un número finito")
    if minimo is not None and x < minimo:
        raise ValueError(f"'{campo}' no puede ser negativo")
    return x


def _fecha(v):
    if isinstance(v, datetime):
        d = v.date()
    elif isinstance(v, date):
        d = v
    else:
        t = str(v or "").strip()
        if not t:
            raise ValueError("falta 'fecha'")
        try:
            d = date.fromisoformat(t[:10])
        except ValueError:
            raise ValueError("'fecha' debe venir como AAAA-MM-DD") from None
    if d.year < 1990 or d > date.today() + timedelta(days=400):
        raise ValueError("'fecha' fuera de rango")
    return d


def validar_filas(filas, entidad_obligatoria: bool, roles: dict | None = None):
    """Normaliza filas (dict con nombres flexibles o los del archivo original). Devuelve lista de dict con
    COLUMNAS; lanza FilasInvalidas."""
    if not isinstance(filas, list):
        raise FilasInvalidas([{"fila": None, "motivo": "se esperaba una lista de filas"}])
    if not filas:
        raise FilasInvalidas([{"fila": None, "motivo": "no viene ninguna fila"}])
    if len(filas) > MAX_FILAS_POR_ENVIO:
        raise FilasInvalidas([{"fila": None, "motivo": f"máximo {MAX_FILAS_POR_ENVIO} filas por envío"}])
    alias = {s: canon for canon, ss in SINONIMOS.items() for s in ss}
    alias.update({_norm(col): rol for rol, col in (roles or {}).items() if col and rol in COLUMNAS})
    salida, errores = {}, []
    for i, f in enumerate(filas):
        if not isinstance(f, dict):
            errores.append({"fila": i, "motivo": "cada fila debe ser un objeto"})
            continue
        d = {}
        for k, v in f.items():
            c = alias.get(_norm(k))
            if c and c not in d:
                d[c] = v
        try:
            fecha = _fecha(d.get("fecha"))
            ent = str(d.get("entidad") or "").strip()[:200]
            if entidad_obligatoria and not ent:
                raise ValueError("falta 'entidad' (este pronóstico tiene varias series)")
            if not entidad_obligatoria:
                ent = ""
            obj = _numero(d.get("objetivo"), "ventas")
            inv = _numero(d.get("inventario"), "inventario", 0)
            pre = _numero(d.get("precio"), "precio", 0)
            if obj is None and inv is None and pre is None:
                raise ValueError("la fila no trae ventas, inventario ni precio")
        except ValueError as e:
            errores.append({"fila": i, "motivo": str(e)})
            continue
        clave = (fecha.isoformat(), ent)
        previa = salida.get(clave, {})
        nueva = {"fecha": clave[0], "entidad": ent}
        for c, v in (("objetivo", obj), ("inventario", inv), ("precio", pre)):
            nueva[c] = v if v is not None else previa.get(c)
        salida[clave] = nueva
    if errores:
        raise FilasInvalidas(errores[:50])
    return list(salida.values())


# ---------------------------------------------------------------- almacenamiento

class _Base:
    def roles_registro(self, registro_id) -> dict:
        return dict((self.config_registro(registro_id) or {}).get("roles") or {})

    def resumen(self, usuario, registro_id):
        filas = self.leer_filas(usuario, registro_id)
        con_ventas = [f for f in filas if f.get("objetivo") is not None]
        return dict(n=len(filas), n_ventas=len(con_ventas),
                    ultima_fecha=max((f["fecha"] for f in filas), default=None),
                    ultimo_recibido=max((str(f["recibido"]) for f in filas), default=None))


class AlmacenVivoLocal(_Base):
    """SQLite, en el mismo archivo que Mis pronósticos local."""

    def __init__(self, ruta="almacen_local/pronosticos.db"):
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        self.ruta = ruta
        with self._con() as c:
            c.execute("""create table if not exists integraciones (registro_id text primary key, usuario text not null,
                clave_hash text not null unique, prefijo text not null, activa integer not null default 1,
                creado text, ultimo_uso text)""")
            c.execute("""create table if not exists datos_vivo (id integer primary key autoincrement,
                registro_id text not null, usuario text not null, fecha text not null, entidad text not null default '',
                objetivo real, inventario real, precio real, origen text, recibido text,
                unique (registro_id, fecha, entidad))""")

    def _con(self):
        return sqlite3.connect(self.ruta)

    def config_registro(self, registro_id):
        try:
            with self._con() as c:
                r = c.execute("select config from pronosticos where id=?", (registro_id,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return json.loads(r[0]) if r and r[0] else None

    def claves(self, usuario):
        with self._con() as c:
            filas = c.execute("select registro_id, prefijo, activa, creado, ultimo_uso from integraciones "
                              "where usuario=?", (usuario,)).fetchall()
        return {r[0]: dict(prefijo=r[1], activa=bool(r[2]), creado=r[3], ultimo_uso=r[4]) for r in filas}

    def crear_clave(self, usuario, registro_id):
        clave = nueva_clave()
        with self._con() as c:
            c.execute("delete from integraciones where registro_id=? and usuario=?", (registro_id, usuario))
            c.execute("insert into integraciones values (?,?,?,?,1,?,null)",
                      (registro_id, usuario, hash_clave(clave), clave[:10], _ahora()))
        return clave

    def desactivar_clave(self, usuario, registro_id):
        with self._con() as c:
            c.execute("delete from integraciones where registro_id=? and usuario=?", (registro_id, usuario))

    def dueno_de_clave(self, clave):
        with self._con() as c:
            r = c.execute("select usuario, registro_id from integraciones where clave_hash=? and activa=1",
                          (hash_clave(clave),)).fetchone()
            if r:
                c.execute("update integraciones set ultimo_uso=? where registro_id=?", (_ahora(), r[1]))
        return (r[0], r[1]) if r else None

    def guardar_filas(self, usuario, registro_id, filas, origen="api"):
        ahora = _ahora()
        with self._con() as c:
            for f in filas:
                cols = [k for k in CAMPOS_VALOR if f.get(k) is not None]
                sets = ", ".join([f"{k}=excluded.{k}" for k in cols] + ["origen=excluded.origen",
                                                                       "recibido=excluded.recibido"])
                c.execute(f"insert into datos_vivo (registro_id, usuario, fecha, entidad, {', '.join(cols)}, origen, "
                          f"recibido) values (?,?,?,?{',?' * len(cols)},?,?) on conflict(registro_id, fecha, entidad) "
                          f"do update set {sets}",
                          [registro_id, usuario, f["fecha"], f["entidad"]] + [f[k] for k in cols] + [origen, ahora])
        return len(filas)

    def leer_filas(self, usuario, registro_id, limite=None):
        q = ("select fecha, entidad, objetivo, inventario, precio, origen, recibido from datos_vivo "
             "where usuario=? and registro_id=? order by fecha, entidad")
        with self._con() as c:
            filas = c.execute(q, (usuario, registro_id)).fetchall()
        out = [dict(zip(COLUMNAS + ("origen", "recibido"), r)) for r in filas]
        return out[-limite:] if limite else out

    def borrar_filas(self, usuario, registro_id, claves):
        with self._con() as c:
            c.executemany("delete from datos_vivo where usuario=? and registro_id=? and fecha=? and entidad=?",
                          [(usuario, registro_id, f, e) for f, e in claves])

    def borrar_todo(self, usuario, registro_id):
        with self._con() as c:
            c.execute("delete from datos_vivo where usuario=? and registro_id=?", (usuario, registro_id))
            c.execute("delete from integraciones where usuario=? and registro_id=?", (usuario, registro_id))


class AlmacenVivoSupabase(_Base):
    """Tablas integraciones y datos_vivo en el Postgres de Supabase (API REST, service_role)."""

    PAGINA = 1000

    def __init__(self, url, key, timeout=30):
        self.base = url.rstrip("/") + "/rest/v1/"
        self.h = {"apikey": key, "Content-Type": "application/json",
                  **({"Authorization": f"Bearer {key}"} if key.startswith("eyJ") else {})}
        self.timeout = timeout

    def _req(self, metodo, tabla, **kw):
        h = dict(self.h, **kw.pop("headers", {}))
        r = requests.request(metodo, self.base + tabla, headers=h, timeout=self.timeout, **kw)
        r.raise_for_status()
        return r

    def config_registro(self, registro_id):
        filas = self._req("GET", "pronosticos", params={"id": f"eq.{registro_id}", "select": "config"}).json()
        return filas[0]["config"] if filas else None

    def claves(self, usuario):
        filas = self._req("GET", "integraciones", params={"usuario": f"eq.{usuario}",
                          "select": "registro_id,prefijo,activa,creado,ultimo_uso"}).json()
        return {f.pop("registro_id"): f for f in filas}

    def crear_clave(self, usuario, registro_id):
        clave = nueva_clave()
        self.desactivar_clave(usuario, registro_id)
        self._req("POST", "integraciones", json=dict(registro_id=registro_id, usuario=usuario,
                                                     clave_hash=hash_clave(clave), prefijo=clave[:10]))
        return clave

    def desactivar_clave(self, usuario, registro_id):
        self._req("DELETE", "integraciones", params={"usuario": f"eq.{usuario}", "registro_id": f"eq.{registro_id}"})

    def dueno_de_clave(self, clave):
        filas = self._req("GET", "integraciones", params={"clave_hash": f"eq.{hash_clave(clave)}",
                          "activa": "eq.true", "select": "usuario,registro_id"}).json()
        if not filas:
            return None
        f = filas[0]
        self._req("PATCH", "integraciones", params={"registro_id": f"eq.{f['registro_id']}"},
                  json={"ultimo_uso": _ahora()})
        return f["usuario"], f["registro_id"]

    def guardar_filas(self, usuario, registro_id, filas, origen="api"):
        ahora = _ahora()
        grupos = {}
        for f in filas:     # PostgREST exige las mismas columnas en cada lote; solo se actualizan las enviadas
            cols = tuple(k for k in CAMPOS_VALOR if f.get(k) is not None)
            grupos.setdefault(cols, []).append(
                dict(registro_id=registro_id, usuario=usuario, fecha=f["fecha"], entidad=f["entidad"],
                     origen=origen, recibido=ahora, **{k: f[k] for k in cols}))
        for lote in grupos.values():
            for i in range(0, len(lote), self.PAGINA):
                self._req("POST", "datos_vivo", params={"on_conflict": "registro_id,fecha,entidad"},
                          headers={"Prefer": "resolution=merge-duplicates,return=minimal"}, json=lote[i:i + self.PAGINA])
        return len(filas)

    def leer_filas(self, usuario, registro_id, limite=None):
        out, desde = [], 0
        params = {"usuario": f"eq.{usuario}", "registro_id": f"eq.{registro_id}",
                  "select": "fecha,entidad,objetivo,inventario,precio,origen,recibido", "order": "fecha,entidad,id"}
        while True:
            lote = self._req("GET", "datos_vivo", params=dict(params, limit=self.PAGINA, offset=desde)).json()
            out += lote
            if len(lote) < self.PAGINA:
                break
            desde += self.PAGINA
        return out[-limite:] if limite else out

    def borrar_filas(self, usuario, registro_id, claves):
        for f, e in claves:
            self._req("DELETE", "datos_vivo", params={"usuario": f"eq.{usuario}", "registro_id": f"eq.{registro_id}",
                                                      "fecha": f"eq.{f}", "entidad": f"eq.{e}"})

    def borrar_todo(self, usuario, registro_id):
        pass    # las tablas borran en cascada al borrar el pronóstico


def crear(config: dict | None):
    config = config or {}
    if config.get("tipo") == "supabase":
        return AlmacenVivoSupabase(config["url"], config["key"])
    return AlmacenVivoLocal(os.path.join(config.get("ruta", "almacen_local"), "pronosticos.db"))


# ---------------------------------------------------------------- link a una planilla o CSV

def url_descarga(url: str) -> str:
    """Convierte un link de Google Sheets en su exportación CSV (respeta la pestaña gid)."""
    u = url.strip()
    m = re.match(r"https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,})", u)
    if m and "/d/e/" not in u:
        gid = re.search(r"[#&?]gid=(\d+)", u)
        return f"https://docs.google.com/spreadsheets/d/{m[1]}/export?format=csv" + (f"&gid={gid[1]}" if gid else "")
    return u


def _host_publico(url):
    p = urlparse(url)
    if p.scheme != "https" or not p.hostname:
        raise ValueError("El link debe empezar con https://")
    try:
        ips = {a[4][0] for a in socket.getaddrinfo(p.hostname, 443)}
    except socket.gaierror:
        raise ValueError("No se encontró ese sitio. Revisa el link.") from None
    for ip in ips:
        d = ipaddress.ip_address(ip)
        if d.is_private or d.is_loopback or d.is_link_local or d.is_reserved or d.is_multicast:
            raise ValueError("Ese link no apunta a un sitio público.")


def descargar_link(url: str) -> tuple[str, bytes]:
    """Descarga un CSV / Excel público. Devuelve (nombre para leerlo, contenido)."""
    actual = url_descarga(url)
    for _ in range(6):
        _host_publico(actual)
        r = requests.get(actual, timeout=20, allow_redirects=False, stream=True,
                         headers={"User-Agent": "motor-predictivo"})
        if r.is_redirect:
            actual = urljoin(actual, r.headers.get("Location", ""))
            continue
        if r.status_code in (401, 403, 404):
            raise ValueError("No se pudo leer el link. Si es una planilla de Google, compártela como "
                             "\"Cualquier persona con el enlace puede ver\".")
        r.raise_for_status()
        contenido = b""
        for trozo in r.iter_content(65536):
            contenido += trozo
            if len(contenido) > MAX_BYTES_LINK:
                raise ValueError("El archivo del link pesa más de 15 MB.")
        tipo = r.headers.get("Content-Type", "")
        if "text/html" in tipo:
            raise ValueError("El link abre una página, no un archivo. Si es Google Sheets, compártela como "
                             "\"Cualquier persona con el enlace puede ver\".")
        excel = "spreadsheetml" in tipo or "ms-excel" in tipo or urlparse(actual).path.lower().endswith((".xlsx", ".xls"))
        return ("link.xlsx" if excel else "link.csv"), contenido
    raise ValueError("El link redirige demasiadas veces.")


def filas_desde_tabla(df, roles: dict):
    """Lleva una tabla (formato del archivo original o columnas fecha/entidad/ventas/...) a filas en vivo."""
    import pandas as pd
    from motor import datos as D
    origen = {}
    for canon in ("fecha", "entidad", "objetivo", "inventario", "precio"):
        col = roles.get(canon)
        if col and col in df.columns:
            origen[canon] = col
    if "fecha" not in origen or "objetivo" not in origen:
        por_nombre = {_norm(c): c for c in df.columns}
        for canon, ss in SINONIMOS.items():
            if canon not in origen:
                col = next((por_nombre[s] for s in ss if s in por_nombre), None)
                if col is not None:
                    origen[canon] = col
    if "fecha" not in origen or not ({"objetivo", "inventario"} & set(origen)):
        raise ValueError("La planilla necesita al menos una columna de fecha y una de ventas (o inventario).")
    if roles.get("entidad") and "entidad" not in origen:
        raise ValueError(f"La planilla necesita la columna '{roles['entidad']}' para saber a qué serie va cada fila.")
    fechas, _ = D.parsear_fechas(df[origen["fecha"]])
    out = pd.DataFrame({"fecha": fechas.dt.strftime("%Y-%m-%d")})
    out["entidad"] = df[origen["entidad"]].astype(str).str.strip() if roles.get("entidad") else ""
    for c in CAMPOS_VALOR:
        out[c] = D.a_numero(df[origen[c]]) if c in origen else float("nan")
    out = out[out["fecha"].notna() & out[list(CAMPOS_VALOR)].notna().any(axis=1)]
    # datos transaccionales: una fila por fecha y serie (ventas sumadas, último inventario, precio promedio)
    out = out.groupby(["fecha", "entidad"], as_index=False, sort=False).agg(
        objetivo=("objetivo", lambda x: x.sum(min_count=1)), inventario=("inventario", "last"),
        precio=("precio", "mean"))
    return [{k: (None if (isinstance(v, float) and math.isnan(v)) else v) for k, v in r.items()}
            for r in out.to_dict("records")]


# ---------------------------------------------------------------- mezcla con el archivo original

def _tabla_filas(filas, con_entidad):
    import pandas as pd
    t = pd.DataFrame(list(filas), columns=list(COLUMNAS))
    if t.empty:
        return t
    t["fecha"] = pd.to_datetime(t["fecha"], errors="coerce")
    t["entidad"] = t["entidad"].fillna("").astype(str).str.strip() if con_entidad else ""
    for c in CAMPOS_VALOR:
        t[c] = pd.to_numeric(t[c], errors="coerce")
    t = t[t["fecha"].notna()]
    return t.drop_duplicates(["fecha", "entidad"], keep="last")     # en orden de prioridad: la última gana


def _base_por_periodo(df_base, roles, frecuencia):
    from motor import datos as D
    fechas, _ = D.parsear_fechas(df_base[roles["fecha"]])
    periodo = D.alinear_fechas(fechas, frecuencia)
    ent = df_base[roles["entidad"]].astype(str).str.strip() if roles.get("entidad") else ""
    return fechas, periodo, ent


def cambios_pendientes(df_base, roles, filas, frecuencia) -> dict:
    """Períodos con ventas en vivo que no están en el archivo o que cambian su total."""
    import pandas as pd
    from motor import datos as D
    t = _tabla_filas(filas, bool(roles.get("entidad")))
    t = t[t["objetivo"].notna()] if len(t) else t
    if t.empty:
        return dict(n=0, nuevos=0, hasta=None, periodos=0, series=0)
    _, periodo, ent = _base_por_periodo(df_base, roles, frecuencia)
    base = pd.DataFrame({"p": periodo, "e": ent, "v": D.a_numero(df_base[roles["objetivo"]])})
    base = base.dropna(subset=["p"]).groupby(["e", "p"])["v"].sum(min_count=1)
    t = t.assign(p=D.alinear_fechas(t["fecha"], frecuencia))
    vivo = t.groupby(["entidad", "p"])["objetivo"].sum()
    n = nuevos = 0
    for k, v in vivo.items():
        if k not in base.index:
            n, nuevos = n + 1, nuevos + 1
        elif pd.isna(base[k]) or abs(base[k] - v) > 1e-9 * max(1.0, abs(v)):
            n += 1
    cambiados = [k for k, v in vivo.items() if k not in base.index or pd.isna(base[k])
                 or abs(base[k] - v) > 1e-9 * max(1.0, abs(v))]
    hasta = t["fecha"].max() if n else None
    return dict(n=n, nuevos=nuevos, hasta=hasta, periodos=len({p for _, p in cambiados}),
                series=len({e for e, _ in cambiados}))


def combinar(df_base, roles, filas, frecuencia):
    """Archivo original + filas con ventas: reemplaza los períodos que traen y agrega los nuevos.
    Devuelve el DataFrame con las columnas originales y la fecha en formato AAAA-MM-DD."""
    import pandas as pd
    from motor import datos as D
    t = _tabla_filas(filas, bool(roles.get("entidad")))
    t = t[t["objetivo"].notna()] if len(t) else t
    base = df_base.copy()
    fechas, periodo, ent = _base_por_periodo(base, roles, frecuencia)
    if len(t):
        p_vivo = D.alinear_fechas(t["fecha"], frecuencia)
        tocados = set(zip(t["entidad"], p_vivo))
        llave = list(zip(ent if roles.get("entidad") else [""] * len(base), periodo))
        quedan = [k not in tocados for k in llave]
        base, fechas = base[quedan], fechas[quedan]
    base[roles["fecha"]] = fechas.dt.strftime("%Y-%m-%d")
    if len(t):
        nuevas = pd.DataFrame(index=range(len(t)), columns=base.columns, dtype=object)
        nuevas[roles["fecha"]] = t["fecha"].dt.strftime("%Y-%m-%d").to_numpy()
        nuevas[roles["objetivo"]] = t["objetivo"].to_numpy()
        if roles.get("entidad"):
            nuevas[roles["entidad"]] = t["entidad"].to_numpy()
        for rol in ("inventario", "precio"):
            if roles.get(rol) and roles[rol] in nuevas.columns:
                nuevas[roles[rol]] = t[rol].to_numpy()
        base = pd.concat([base, nuevas], ignore_index=True)
    orden = pd.to_datetime(base[roles["fecha"]], errors="coerce")
    return base.assign(_o=orden).sort_values("_o", kind="stable").drop(columns="_o").reset_index(drop=True)


def filas_de_editor(tabla):
    """Filas escritas a mano en la tabla del formulario (columna ventas → objetivo)."""
    import pandas as pd
    out = []
    for f in tabla.to_dict("records"):
        f = {k: (None if v is None or (not isinstance(v, str) and pd.isna(v)) else v) for k, v in f.items()}
        if all(f.get(k) is None for k in ("ventas", "inventario", "precio")):
            continue
        if f.get("fecha") is not None:
            f["fecha"] = pd.Timestamp(f["fecha"]).date().isoformat()
        out.append(f)
    return out


def inventario_reciente(filas, con_entidad=True) -> dict:
    """{entidad: (inventario, fecha)} con la lectura más nueva de cada una."""
    out = {}
    for f in filas:
        if f.get("inventario") is None:
            continue
        e = (str(f.get("entidad") or "").strip() if con_entidad else "") or "Serie única"
        if e not in out or str(f["fecha"]) >= out[e][1]:
            out[e] = (float(f["inventario"]), str(f["fecha"])[:10])
    return out

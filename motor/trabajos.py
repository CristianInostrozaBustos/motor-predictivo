"""Cola de entrenamientos en segundo plano.

El sitio anota un trabajo (tabla `trabajos`) y avisa a GitHub Actions (workflow_dispatch). El entrenador
(entrenador.py) lo toma, entrena y deja el estado en la tabla; el sitio lo consulta cada pocos segundos.
Estados: pendiente → corriendo → listo / error.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone

import requests

ACTIVOS = ("pendiente", "corriendo")
CAMPOS = ("id", "usuario", "registro_id", "estado", "origen", "progreso", "mensaje", "creado", "iniciado", "terminado")

SQL_SUPABASE = """
-- Entrenamientos en segundo plano (GitHub Actions)
create table if not exists public.trabajos (
    id           uuid primary key default gen_random_uuid(),
    usuario      text not null,
    registro_id  uuid not null references public.pronosticos(id) on delete cascade,
    estado       text not null default 'pendiente',   -- pendiente / corriendo / listo / error
    origen       text not null default 'pedido',      -- pedido / nuevo / programado
    progreso     double precision not null default 0,
    mensaje      text,
    creado       timestamptz not null default now(),
    iniciado     timestamptz,
    terminado    timestamptz
);
create index if not exists trabajos_registro_idx on public.trabajos (registro_id, creado desc);
alter table public.trabajos enable row level security;
grant select, insert, update, delete on table public.trabajos to service_role;
revoke all on table public.trabajos from anon, authenticated;
""".strip()


def ahora():
    return datetime.now(timezone.utc).isoformat()


class TrabajosLocal:
    def __init__(self, ruta="almacen_local/pronosticos.db"):
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        self.ruta = ruta
        with self._con() as c:
            c.execute("""create table if not exists trabajos (id text primary key, usuario text not null,
                registro_id text not null, estado text not null default 'pendiente', origen text not null default 'pedido',
                progreso real not null default 0, mensaje text, creado text, iniciado text, terminado text)""")

    def _con(self):
        return sqlite3.connect(self.ruta)

    def _filas(self, q, args=()):
        with self._con() as c:
            return [dict(zip(CAMPOS, r)) for r in c.execute(f"select {','.join(CAMPOS)} from trabajos {q}", args)]

    def crear(self, usuario, registro_id, origen="pedido"):
        t = dict(id=str(uuid.uuid4()), usuario=usuario, registro_id=registro_id, estado="pendiente", origen=origen,
                 progreso=0.0, mensaje=None, creado=ahora(), iniciado=None, terminado=None)
        with self._con() as c:
            c.execute(f"insert into trabajos ({','.join(CAMPOS)}) values ({','.join('?' * len(CAMPOS))})",
                      [t[k] for k in CAMPOS])
        return t

    def obtener(self, id_):
        f = self._filas("where id=?", (id_,))
        return f[0] if f else None

    def ultimo(self, registro_id):
        f = self._filas("where registro_id=? order by creado desc limit 1", (registro_id,))
        return f[0] if f else None

    def pendientes(self):
        return self._filas("where estado='pendiente' order by creado")

    def terminados(self, registro_id, n=3):
        return self._filas("where registro_id=? and estado='listo' and terminado is not null order by creado desc "
                           "limit ?", (registro_id, n))

    def actualizar(self, id_, **cambios):
        sets = ", ".join(f"{k}=?" for k in cambios)
        with self._con() as c:
            c.execute(f"update trabajos set {sets} where id=?", list(cambios.values()) + [id_])


class TrabajosSupabase:
    def __init__(self, url, key, timeout=30):
        self.base = url.rstrip("/") + "/rest/v1/trabajos"
        self.h = {"apikey": key, "Content-Type": "application/json",
                  **({"Authorization": f"Bearer {key}"} if key.startswith("eyJ") else {})}
        self.timeout = timeout

    def _get(self, params):
        r = requests.get(self.base, headers=self.h, params=dict(params, select="*"), timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def crear(self, usuario, registro_id, origen="pedido"):
        r = requests.post(self.base, headers=dict(self.h, Prefer="return=representation"), timeout=self.timeout,
                          json=dict(usuario=usuario, registro_id=registro_id, origen=origen))
        r.raise_for_status()
        return r.json()[0]

    def obtener(self, id_):
        f = self._get({"id": f"eq.{id_}"})
        return f[0] if f else None

    def ultimo(self, registro_id):
        f = self._get({"registro_id": f"eq.{registro_id}", "order": "creado.desc", "limit": 1})
        return f[0] if f else None

    def pendientes(self):
        return self._get({"estado": "eq.pendiente", "order": "creado"})

    def terminados(self, registro_id, n=3):
        return self._get({"registro_id": f"eq.{registro_id}", "estado": "eq.listo", "terminado": "not.is.null",
                          "order": "creado.desc", "limit": n})

    def actualizar(self, id_, **cambios):
        r = requests.patch(self.base, headers=self.h, params={"id": f"eq.{id_}"}, json=cambios, timeout=self.timeout)
        r.raise_for_status()


def crear(config: dict | None):
    config = config or {}
    if config.get("tipo") == "supabase":
        return TrabajosSupabase(config["url"], config["key"])
    return TrabajosLocal(os.path.join(config.get("ruta", "almacen_local"), "pronosticos.db"))


def duracion_estimada(cola, registro_id, defecto_seg=420.0) -> float:
    """Segundos que tomaron las últimas actualizaciones de este análisis (mediana), o un valor típico."""
    import statistics
    try:
        durs = [(datetime.fromisoformat(str(t["terminado"]).replace("Z", "+00:00"))
                 - datetime.fromisoformat(str(t["creado"]).replace("Z", "+00:00"))).total_seconds()
                for t in cola.terminados(registro_id)]
    except Exception:  # noqa: BLE001
        durs = []
    durs = [d for d in durs if d > 0]
    return statistics.median(durs) if durs else defecto_seg


def disparar_github(token: str, repo: str, trabajo_id: str, workflow="entrenar.yml", rama="main"):
    """Pide a GitHub Actions que corra el entrenador para este trabajo. Lanza RuntimeError con el motivo."""
    r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"},
                      json={"ref": rama, "inputs": {"trabajo": trabajo_id}}, timeout=20)
    if r.status_code != 204:
        motivo = {401: "el token de GitHub no es válido", 403: "el token no tiene permiso para Actions",
                  404: "no se encontró el repositorio o el workflow",
                  422: "el workflow no acepta el pedido (¿está en la rama main?)"}.get(r.status_code, r.text[:200])
        raise RuntimeError(f"GitHub respondió {r.status_code}: {motivo}")

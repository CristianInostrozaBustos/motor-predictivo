# La API de los datos en vivo del Motor Predictivo (FastAPI, se publica en Render).

import os
import sys

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from motor import vivo as V 

app = FastAPI(title="Motor Predictivo · API de datos", version="1.0",
              description="Envía ventas, inventario o precios nuevos a un pronóstico guardado.")


def _almacen():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if url and key:
        return V.crear({"tipo": "supabase", "url": url, "key": key})
    return V.crear({"ruta": os.environ.get("RUTA_LOCAL", "almacen_local")})


ALMACEN = _almacen()


def _clave(authorization, x_clave):
    clave = (x_clave or "").strip()
    if not clave and authorization and authorization.lower().startswith("bearer "):
        clave = authorization[7:].strip()
    if not clave.startswith(V.PREFIJO_CLAVE):
        raise HTTPException(401, "Falta la clave de integración (header Authorization: Bearer mp_...).")
    try:
        dueno = ALMACEN.dueno_de_clave(clave)
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "No se pudo conectar con la base de datos. Intenta de nuevo.") from None
    if not dueno:
        raise HTTPException(401, "Clave de integración inválida o desactivada.")
    return dueno


@app.get("/")
def inicio():
    return {"servicio": "Motor Predictivo · API de datos", "documentacion": "/docs", "salud": "/salud"}


@app.get("/salud")
def salud():
    return {"ok": True}


@app.post("/v1/datos")
def recibir(filas: list | dict = Body(..., examples=[{"filas": [
                {"fecha": "2026-10-04", "entidad": "SKU-001", "ventas": 120, "inventario": 850}]}]),
            authorization: str | None = Header(None), x_clave: str | None = Header(None, alias="X-Clave")):
    """Guarda filas nuevas. Cada fila: fecha (AAAA-MM-DD), entidad (si el pronóstico tiene varias series) y al
    menos uno de ventas, inventario o precio. Si una fecha y entidad ya existe, se reemplaza."""
    usuario, registro = _clave(authorization, x_clave)
    lista = filas.get("filas") if isinstance(filas, dict) else filas
    try:
        roles = ALMACEN.roles_registro(registro)
        limpias = V.validar_filas(lista, bool(roles.get("entidad")), roles)
    except V.FilasInvalidas as e:
        return JSONResponse(status_code=422, content={"guardadas": 0, "errores": e.errores})
    try:
        n = ALMACEN.guardar_filas(usuario, registro, limpias, origen="api")
    except Exception:
        raise HTTPException(503, "No se pudieron guardar las filas. Intenta de nuevo.") from None
    return {"guardadas": n, "con_ventas": sum(f["objetivo"] is not None for f in limpias),
            "con_inventario": sum(f["inventario"] is not None for f in limpias)}


@app.get("/v1/datos")
def listar(limite: int = Query(100, ge=1, le=5000), authorization: str | None = Header(None),
           x_clave: str | None = Header(None, alias="X-Clave")):
    """Últimas filas recibidas para este pronóstico (para revisar que llegaron)."""
    usuario, registro = _clave(authorization, x_clave)
    filas = ALMACEN.leer_filas(usuario, registro)
    filas = sorted(filas, key=lambda f: str(f.get("recibido")))[-limite:]
    return {"total": len(filas), "filas": filas}

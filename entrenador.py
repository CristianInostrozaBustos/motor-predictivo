"""Entrenador en segundo plano (corre en GitHub Actions).

    python entrenador.py                 # TRABAJO=<id> en el entorno: ese trabajo; vacío: modo programado
    python entrenador.py --programado    # revisa todos los análisis conectados y entrena los que tengan datos nuevos

Variables de entorno: SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_BUCKET (por defecto "motor").
Sin ellas usa la carpeta local almacen_local/ (para probar).
"""

import logging
import os
import sys
import time
import traceback

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("entrenador")

from motor import almacen as A  # noqa: E402
from motor import repositorio as Rp  # noqa: E402
from motor import servicio as Sv  # noqa: E402
from motor import trabajos as T  # noqa: E402
from motor import vivo as V  # noqa: E402


def conexiones():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    cfg = ({"tipo": "supabase", "url": url, "key": key, "bucket": os.environ.get("SUPABASE_BUCKET") or "motor"}
           if url and key else {"ruta": os.environ.get("RUTA_LOCAL", "almacen_local")})
    return A.crear(cfg), Rp.crear(cfg), V.crear(cfg), T.crear(cfg)


def correr_trabajo(trabajo, alm, repo, vivo, cola):
    reg = repo.obtener(trabajo["usuario"], trabajo["registro_id"])
    if reg is None:
        cola.actualizar(trabajo["id"], estado="error", mensaje="El análisis ya no existe.", terminado=T.ahora())
        return
    cola.actualizar(trabajo["id"], estado="corriendo", iniciado=T.ahora(), progreso=0.01, mensaje="Preparando…")
    ultimo = [0.0]

    def progreso(frac, texto):
        if time.time() - ultimo[0] > 10:       # no saturar la base con cada paso
            ultimo[0] = time.time()
            log.info("%3.0f%% %s", 100 * frac, texto)
            try:
                actual = cola.obtener(trabajo["id"])
                cola.actualizar(trabajo["id"], progreso=float(min(frac, 0.99)), mensaje=str(texto)[:200])
            except Exception:  # noqa: BLE001
                return
            if actual and actual["estado"] == "cancelado":
                raise T.Cancelado()

    try:
        def revisar_corte():
            t = cola.obtener(trabajo["id"])
            if t and t["estado"] == "cancelado":
                raise T.Cancelado()

        clave, origen = Sv.entrenar_registro(reg, alm, repo, vivo, V.descargar_link, progreso,
                                             incorporar=trabajo.get("origen") != "nuevo", revisar_corte=revisar_corte)
        cola.actualizar(trabajo["id"], estado="listo", progreso=1.0, terminado=T.ahora(),
                        mensaje=f"Listo ({'modelo nuevo' if origen == 'nuevo' else 'modelo ya existía'})")
        log.info("trabajo %s listo: %s (%s)", trabajo["id"], clave, origen)
    except T.Cancelado:
        log.info("trabajo %s cancelado por el usuario", trabajo["id"])
    except Exception as e:  # noqa: BLE001
        log.error(traceback.format_exc())
        cola.actualizar(trabajo["id"], estado="error", terminado=T.ahora(),
                        mensaje=(str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}")[:300])


def programado(alm, repo, vivo, cola):
    """Análisis con planilla conectada o filas en vivo, en modo automático (o con su frecuencia cumplida)."""
    for t in cola.pendientes():              # trabajos que quedaron sin correr (aviso a GitHub perdido)
        correr_trabajo(t, alm, repo, vivo, cola)
    for reg in repo.listar_todos():
        cfg = reg.get("config") or {}
        v = Sv.config_vivo(cfg)
        if not Sv.toca_actualizar_solo(v):
            continue
        try:
            if not v.get("link") and not vivo.leer_filas(reg["usuario"], reg["id"]):
                continue
            estado = Sv.revisar(reg, alm, vivo, V.descargar_link)
        except Exception as e:  # noqa: BLE001
            log.warning("no se pudo revisar %s: %s", reg["id"], e)
            continue
        if not estado["pendiente"]:
            continue
        ult = cola.ultimo(reg["id"])
        if ult and ult["estado"] in T.ACTIVOS:
            continue
        log.info("datos nuevos en %s (%s): entrenando", reg["id"], reg["nombre"])
        correr_trabajo(cola.crear(reg["usuario"], reg["id"], "programado"), alm, repo, vivo, cola)


def main():
    alm, repo, vivo, cola = conexiones()
    id_ = (os.environ.get("TRABAJO") or "").strip()
    if "--programado" in sys.argv or not id_:
        programado(alm, repo, vivo, cola)
        return
    t = cola.obtener(id_)
    if t is None:
        log.error("no existe el trabajo %s", id_)
        sys.exit(1)
    if t["estado"] in ("listo", "corriendo", "cancelado"):
        log.info("el trabajo %s ya está %s", id_, t["estado"])
        return
    correr_trabajo(t, alm, repo, vivo, cola)


if __name__ == "__main__":
    main()

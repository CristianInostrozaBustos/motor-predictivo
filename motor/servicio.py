"""Pasos del pronóstico sin Streamlit: los usa el sitio y el entrenador en segundo plano (GitHub Actions).

Un análisis guardado = registro (repositorio) + archivo de datos (almacén) + filas en vivo (planilla o API).
"""

from __future__ import annotations

import hashlib
import os

import pandas as pd

from . import datos as D

SUAVIZAR_PICOS_DEFECTO = True


def clave_dataset(dp) -> str:
    """Huella de los datos preparados: identifica el modelo guardado. Los valores de clima se excluyen (el último
    tramo cambia de pronóstico a observado de un día a otro); cuenta solo que el clima se usa y de dónde."""
    from . import clima as C
    df = dp.df.drop(columns=[c for c in C.VARIABLES if c in dp.df.columns])
    h = hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes())
    h.update(str(getattr(dp, "clima_lugar", "")).encode())
    h.update(str(dp.variables_modelo).encode())
    h.update(dp.config.frecuencia.encode())
    if dp.config.suavizar_picos and len(dp.picos):     # solo cambia el modelo si hay picos que suavizar
        h.update(b"picos")
    return h.hexdigest()[:16]


def clima_para(df, roles: dict, lugar: dict, frecuencia: str, obtener=None):
    """(df con columnas de clima, clima diario hasta ~1 año después del historial) o (df, None) si no hay lugar.
    obtener(lat, lon, desde, hasta) permite usar una versión con caché."""
    from . import clima as C
    if not lugar or not roles.get("fecha"):
        return df, None
    fechas, _ = D.parsear_fechas(df[roles["fecha"]])
    if fechas.notna().sum() == 0:
        return df, None
    desde = fechas.min() - pd.Timedelta(days=100)
    hasta = fechas.max() + pd.Timedelta(days=400)
    diaria = (obtener or C.serie_diaria)(lugar["lat"], lugar["lon"], desde, hasta)
    return C.agregar(df, fechas, diaria, frecuencia), diaria


def preparar_con_config(df, cfg: dict):
    """Prepara los datos con la configuración guardada de un análisis (la misma que usa la página Datos)."""
    from . import clima as C
    roles = cfg.get("roles", {})
    exogenas = list(cfg.get("exogenas", []))
    lugar = cfg.get("clima")
    diaria = None
    if lugar:
        try:
            df, diaria = clima_para(df, roles, lugar, cfg.get("frecuencia", "D"))
            exogenas += [v for v in C.VARIABLES if v not in exogenas]
        except Exception:  # noqa: BLE001  (sin conexión al servicio de clima se sigue sin clima)
            diaria = None
    c = D.Configuracion(roles=dict(sorted(roles.items())), exogenas=exogenas,
                        frecuencia=cfg.get("frecuencia", "D"), relleno_objetivo=cfg.get("relleno", "interpolar"),
                        negativos_a_cero=cfg.get("negativos", True),
                        suavizar_picos=cfg.get("suavizar_picos", SUAVIZAR_PICOS_DEFECTO))
    dp = D.preparar(df, c)
    if diaria is not None:
        dp.clima_diaria, dp.clima_lugar = diaria, lugar.get("nombre", "")
        C.usar_solo_si_influye(dp, c.frecuencia)
    return dp


def config_vivo(cfg: dict) -> dict:
    v = dict(cfg.get("vivo") or {})
    if cfg.get("vivo_link") and not v.get("link"):          # formato anterior
        v["link"] = cfg["vivo_link"]
    return {"modo": "auto", "cada": "pedido", **v}


def huella_bytes(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()[:20]


def toca_actualizar_solo(vivo: dict) -> bool:
    """Según el modo elegido: siempre, una vez al día / semana, o nunca (solo a pedido)."""
    if vivo.get("modo", "auto") == "auto":
        return True
    dias = {"dia": 1, "semana": 7}.get(vivo.get("cada"))
    if not dias:
        return False
    ultima = pd.Timestamp(vivo["ultima"]) if vivo.get("ultima") else None
    return ultima is None or pd.Timestamp.now(tz="UTC") - ultima >= pd.Timedelta(days=dias)


def _tabla_link(descargar, url):
    nombre, contenido = descargar(url)
    return D.leer_archivo(nombre, contenido), contenido


def revisar(reg, almacen, almacen_vivo, descargar) -> dict:
    """¿Hay datos nuevos para este análisis? dict(pendiente, n, planilla_cambio)."""
    from . import almacen as A
    from . import repositorio as Rp
    from . import vivo as V
    cfg = reg.get("config") or {}
    vivo, roles = config_vivo(cfg), cfg.get("roles") or {}
    contenido = almacen.leer(A.ruta_datos(Rp.id_usuario(reg["usuario"]), reg["id"]))
    if contenido is None:
        return dict(pendiente=False, n=0, planilla_cambio=False)
    base = D.leer_archivo(reg["archivo_nombre"], contenido)
    filas, cambio = [], False
    if vivo.get("link"):
        tabla, bruto = _tabla_link(descargar, vivo["link"])
        cambio = bool(vivo.get("link_base")) and huella_bytes(bruto) != vivo.get("hash")
        filas += V.filas_desde_tabla(tabla, roles)
    filas += almacen_vivo.leer_filas(reg["usuario"], reg["id"])
    n = V.cambios_pendientes(base, roles, filas, cfg.get("frecuencia", "D"))["n"]
    return dict(pendiente=bool(n or cambio), n=n, planilla_cambio=cambio)


def datos_actualizados(reg, almacen, almacen_vivo, descargar):
    """Archivo del análisis + planilla + filas en vivo. Devuelve (df, cfg nuevo, nombre, contenido o None si no
    cambió nada que guardar)."""
    from . import almacen as A
    from . import repositorio as Rp
    from . import vivo as V
    cfg = dict(reg.get("config") or {})
    vivo, roles = config_vivo(cfg), cfg.get("roles") or {}
    ruta = A.ruta_datos(Rp.id_usuario(reg["usuario"]), reg["id"])
    filas, reescribir = [], False
    if vivo.get("link") and vivo.get("link_base"):       # la planilla es el historial completo
        base, bruto = _tabla_link(descargar, vivo["link"])
        vivo["hash"] = huella_bytes(bruto)
        reescribir = True
    else:
        contenido = almacen.leer(ruta)
        if contenido is None:
            raise FileNotFoundError("No se encontró el archivo de datos de este pronóstico.")
        base = D.leer_archivo(reg["archivo_nombre"], contenido)
        if vivo.get("link"):
            filas += V.filas_desde_tabla(_tabla_link(descargar, vivo["link"])[0], roles)
    filas += almacen_vivo.leer_filas(reg["usuario"], reg["id"])
    reescribir = reescribir or bool(filas)
    nuevo = V.combinar(base, roles, filas, cfg.get("frecuencia", "D")) if reescribir else base
    vivo["ultima"] = pd.Timestamp.now(tz="UTC").isoformat()
    cfg["vivo"] = vivo
    cfg.pop("vivo_link", None)
    nombre = os.path.splitext(reg["archivo_nombre"])[0] + ".csv" if reescribir else reg["archivo_nombre"]
    return nuevo, cfg, nombre, (nuevo.to_csv(index=False).encode("utf-8") if reescribir else None)


def entrenar_registro(reg, almacen, repo, almacen_vivo, descargar, progreso=None, incorporar=True, revisar_corte=None):
    """Pipeline completo de un análisis guardado: junta datos nuevos, entrena (o reutiliza) y actualiza el registro.
    Devuelve (clave, origen)."""
    from . import almacen as A
    from . import modelo as M
    from . import reglas as R
    from . import repositorio as Rp
    contenido = None
    if incorporar:
        df, cfg, nombre, contenido = datos_actualizados(reg, almacen, almacen_vivo, descargar)
        if contenido is not None:
            df = D.leer_archivo(nombre, contenido)       # igual a lo que leerá el sitio al abrirlo
    else:
        cfg, nombre = dict(reg.get("config") or {}), reg["archivo_nombre"]
        df = D.leer_archivo(nombre, almacen.leer(A.ruta_datos(Rp.id_usuario(reg["usuario"]), reg["id"])))
    dp = preparar_con_config(df, cfg)
    clave = clave_dataset(dp)
    origen = "guardado"
    if not A.existe_modelo(almacen, clave):
        plan = R.planificar(dp)
        res = M.entrenar_motor(dp, plan, progreso)
        if revisar_corte:
            revisar_corte()
        err = A.guardar(almacen, clave, res)
        if err:
            raise RuntimeError(f"No se pudo guardar el modelo: {err}")
        origen = "nuevo"
        error_pct = res.metricas_entidad["wape"].mean()
    else:
        res, _ = A.cargar(almacen, clave)
        error_pct = res.metricas_entidad["wape"].mean() if res is not None else None
    if revisar_corte:
        revisar_corte()
    if contenido is not None:      # los datos nuevos se guardan solo cuando ya existe su modelo
        almacen.escribir(A.ruta_datos(Rp.id_usuario(reg["usuario"]), reg["id"]), contenido)
    repo.actualizar(reg["usuario"], reg["id"], dict(
        archivo_nombre=nombre, config=cfg, clave_modelo=clave, n_entidades=int(dp.df["entidad"].nunique()),
        error_pct=None if error_pct is None or pd.isna(error_pct) else float(error_pct)))
    return clave, origen

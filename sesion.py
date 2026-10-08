"""Estado compartido entre páginas: dataset activo, modelo entrenado, pronóstico y modo desarrollador."""

import hashlib
import os
import re

import numpy as np
import pandas as pd
import streamlit as st

import estilo as E
from motor import datos as D
from motor import reglas as R

CARPETA_EJEMPLOS = "ejemplos"

EJEMPLOS = {
    "retail_diario_tiendas.csv": ("Retail diario · 8 tiendas",
                                  "3 años de ventas diarias con precio, promoción, stock y lead time."),
    "ventas_semanales.csv": ("Ventas semanales · 3 productos", "2 años de ventas semanales con precio."),
    "transacciones.csv": ("Transacciones · 2 SKUs", "Un año de ventas registradas por transacción."),
    "mensual_serie_unica.csv": ("Demanda mensual · 1 producto", "6 años de demanda mensual."),
    "muchos_skus.csv": ("Catálogo grande · 80 SKUs", "Algo más de un año de demanda diaria de 80 productos."),
}


# ---------------------------------------------------------------- modo desarrollador

def modo_dev() -> bool:
    if st.query_params.get("dev") == "1":
        st.session_state["dev"] = True
    elif st.query_params.get("dev") == "0":
        st.session_state["dev"] = False
    return st.session_state.get("dev", False)


# ---------------------------------------------------------------- lectura y preparación

@st.cache_data(show_spinner="Leyendo el archivo...")
def leer(nombre, contenido):
    return D.leer_archivo(nombre, contenido)


def cargar_ejemplo(archivo):
    with open(os.path.join(CARPETA_EJEMPLOS, archivo), "rb") as fh:
        contenido = fh.read()
    st.session_state["archivo_bytes"] = contenido
    return leer(archivo, contenido)


SUAVIZAR_PICOS_DEFECTO = True


@st.cache_data(show_spinner="Preparando los datos...")
def preparar_cacheado(df, roles_items, exogenas, frecuencia, relleno, negativos, suavizar=SUAVIZAR_PICOS_DEFECTO):
    cfg = D.Configuracion(roles=dict(roles_items), exogenas=list(exogenas), frecuencia=frecuencia,
                          relleno_objetivo=relleno, negativos_a_cero=negativos, suavizar_picos=suavizar)
    return D.preparar(df, cfg)


@st.cache_data(ttl=6 * 3600, show_spinner="Trayendo el clima...")
def _clima_diario(lat, lon, desde, hasta, dia):
    from motor import clima as C
    return C.serie_diaria(lat, lon, pd.Timestamp(desde), pd.Timestamp(hasta))


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def buscar_lugar(nombre):
    from motor import clima as C
    return C.buscar(nombre)


def preparar_dataset(df, roles, exogenas, frecuencia, relleno, negativos, suavizar=SUAVIZAR_PICOS_DEFECTO, lugar=None):
    """Prepara los datos y, si se eligió un lugar, adjunta su clima diario (capa de corto plazo, no entra al modelo)."""
    from motor import servicio as Sv
    st.session_state.pop("_clima_error", None)
    dp = preparar_cacheado(df, tuple(sorted(dict(roles).items())), tuple(exogenas), frecuencia, relleno, negativos,
                           suavizar)
    if lugar:
        try:
            hoy = str(pd.Timestamp.today().date())
            diaria = Sv.clima_para(df, dict(roles), lugar,
                                   obtener=lambda la, lo, d, h: _clima_diario(la, lo, str(d.date()), str(h.date()), hoy))
        except Exception as e:  # noqa: BLE001
            diaria = None
            st.session_state["_clima_error"] = f"No se pudo traer el clima ({type(e).__name__}); se sigue sin él."
        if diaria is not None:
            dp.clima_diaria, dp.clima_lugar = diaria, lugar.get("nombre", "")
    return dp


def clave_dataset(dp) -> str:
    from motor import servicio as Sv
    return Sv.clave_dataset(dp)


# ---------------------------------------------------------------- entrenamiento y pronóstico

@st.cache_resource(show_spinner=False)
def _almacen_modelos():
    """Modelos en memoria, compartidos entre usuarios del mismo servidor (clave = huella del dataset)."""
    return {}


def _config_almacen():
    try:
        return dict(st.secrets["almacen"]) if "almacen" in st.secrets else None
    except Exception:  # noqa: BLE001  (sin archivo de secrets)
        return None


def _huella_config():
    """La configuración entra en la clave del caché: si cambian los secrets, se crea la conexión nueva."""
    return tuple(sorted((k, str(v)) for k, v in (_config_almacen() or {}).items()))


@st.cache_resource(show_spinner=False)
def _crear_almacen(huella):
    from motor import almacen as A
    return A.crear(dict(huella) if huella else None)


@st.cache_resource(show_spinner=False)
def _crear_repositorio(huella):
    from motor import repositorio as Rp
    return Rp.crear(dict(huella) if huella else None)


def almacen_persistente():
    """Dónde se guardan modelos y archivos: Supabase Storage si hay secrets, si no una carpeta local."""
    return _crear_almacen(_huella_config())


def repositorio():
    """Registro de Mis pronósticos: Supabase Postgres si hay secrets, si no SQLite local."""
    return _crear_repositorio(_huella_config())


MAX_MODELOS_EN_MEMORIA = 4


def _a_memoria(clave, res):
    memoria = _almacen_modelos()
    while len(memoria) >= MAX_MODELOS_EN_MEMORIA:
        memoria.pop(next(iter(memoria)))
    memoria[clave] = res


def borrar_analisis(reg, con_modelo=False):
    """Borra un análisis guardado: registro, archivo de datos, datos en vivo y, si se pide, su modelo entrenado."""
    from motor import almacen as A
    from motor import repositorio as Rp
    u = _usuario()
    repositorio().borrar(u["correo"], reg["id"])
    pasos = [lambda: almacen_vivo().borrar_todo(u["correo"], reg["id"]),
             lambda: almacen_persistente().borrar(A.ruta_datos(Rp.id_usuario(u["correo"]), reg["id"]))]
    if con_modelo and reg.get("clave_modelo"):
        pasos.append(lambda: almacen_persistente().borrar(A.ruta_modelo(reg["clave_modelo"])))
        _almacen_modelos().pop(reg["clave_modelo"], None)
    for paso in pasos:
        try:
            paso()
        except Exception:  # noqa: BLE001
            pass
    if (st.session_state.get("registro") or {}).get("id") == reg["id"]:
        for k in ("registro", "resultado", "dp", "df_raw", "config_actual", "nombre_dataset", "archivo_bytes",
                  "link_origen", "_menu_dp"):
            st.session_state.pop(k, None)


def modelo_guardado(clave) -> bool:
    """True si ya existe un modelo entrenado para estos datos (en memoria o guardado)."""
    if clave in _almacen_modelos():
        return True
    from motor import almacen as A
    return A.existe_modelo(almacen_persistente(), clave)


def entrenar(clave, dp, plan, progreso=None):
    """Recupera el modelo si ya existe (memoria → almacén); si no, entrena y lo guarda.

    Devuelve (res, origen) con origen "memoria", "guardado" o "nuevo".
    No se usa st.cache_* directo porque la barra de progreso vive fuera de la función.
    """
    from motor import almacen as A
    memoria = _almacen_modelos()
    if clave in memoria:
        if progreso:
            progreso(1.0, "Listo")
        return memoria[clave], "memoria"
    alm = almacen_persistente()
    if progreso:
        progreso(0.02, "Buscando si estos datos ya se entrenaron…")
    res, err = A.cargar(alm, clave)
    if err:
        st.session_state.setdefault("avisos_almacen", []).append(f"No se pudo leer el modelo guardado: {err}")
    if res is not None:
        _a_memoria(clave, res)
        if progreso:
            progreso(1.0, "Listo")
        return res, "guardado"
    from motor import modelo as M
    res = M.entrenar_motor(dp, plan, progreso)
    _a_memoria(clave, res)
    err = A.guardar(alm, clave, res)
    if err:
        st.session_state.setdefault("avisos_almacen", []).append(f"No se pudo guardar el modelo: {err}")
    return res, "nuevo"


def resultado():
    """(DatasetPreparado, ResultadoModelo) si hay un modelo entrenado para el dataset activo."""
    dp = st.session_state.get("dp")
    r = st.session_state.get("resultado")
    if dp is None or r is None or r["clave"] != clave_dataset(dp):
        return dp, None
    return dp, r["res"]


def horizonte():
    return st.session_state.get("horizonte")


@st.cache_data(show_spinner="Midiendo el efecto del clima...", max_entries=10)
def _clima_validado(clave, lugar, dia):
    from motor import clima as C
    dp, res = st.session_state["dp"], st.session_state["resultado"]["res"]
    return C.validar(dp.df, dp.clima_diaria, dp.config.frecuencia, res.backtest)


def clima_efecto():
    """Efecto del clima por producto, con la columna usa (la capa mejora el error en la prueba con datos pasados).
    None si no hay clima elegido o no hay modelo."""
    dp, res = resultado()
    if res is None or getattr(dp, "clima_diaria", None) is None:
        return None
    try:
        return _clima_validado(st.session_state["resultado"]["clave"], dp.clima_lugar,
                               str(pd.Timestamp.today().date()))
    except Exception as e:  # noqa: BLE001
        st.session_state["_clima_error"] = f"No se pudo medir el efecto del clima ({type(e).__name__})."
        return None


def _con_clima(pron):
    """Ajusta los próximos días según el pronóstico del tiempo (solo productos donde el clima mejora el error)."""
    ef = clima_efecto()
    if ef is None or not ef["usa"].any() or not pron:
        return pron
    from motor import clima as C
    dp = st.session_state["dp"]
    fut = next(iter(pron.values()))["fecha"]
    fac = C.factores(ef, dp.clima_diaria, dp.df["fecha"].unique(), fut, dp.config.frecuencia)
    return C.ajustar(pron, fac)


@st.cache_data(show_spinner="Calculando el pronóstico...", max_entries=20)
def _pronostico_cacheado(clave, h, freq, dia=None):
    res = st.session_state["resultado"]["res"]
    from motor import modelo as M
    return M.pronosticar(res, h, freq)


def pronostico_normal(h=None):
    """Pronóstico de los motores, sin la capa de clima."""
    dp, res = resultado()
    if res is None:
        return None
    return _pronostico_cacheado(st.session_state["resultado"]["clave"], h or horizonte(), dp.config.frecuencia,
                                str(pd.Timestamp.today().date()))


def pronostico(h=None):
    base = pronostico_normal(h)
    return _con_clima(base) if base is not None else None


@st.cache_data(show_spinner="Simulando el escenario...", max_entries=20)
def _pronostico_escenario_cacheado(clave, h, freq, cambios, entidades):
    res = st.session_state["resultado"]["res"]
    from motor import modelo as M
    return M.pronosticar(res, h, freq, cambios=[dict(c) for c in cambios], entidades=list(entidades))


def pronostico_escenario(h, cambios, entidades):
    """Pronóstico con cambios en variables del modelo (escenarios). Sin cambios, devuelve el base."""
    dp, res = resultado()
    if not cambios:
        base = pronostico(h)
        return {e: base[e] for e in entidades}
    cambios_t = tuple(tuple(sorted(c.items())) for c in cambios)
    return _con_clima(_pronostico_escenario_cacheado(st.session_state["resultado"]["clave"], h, dp.config.frecuencia,
                                                     cambios_t, tuple(entidades)))


# ---------------------------------------------------------------- vistas por página (menú lateral)
VISTAS = {
    "pronostico": ["Resumen", "Pronóstico", "Tabla", "Precisión", "Prueba con datos pasados"],
    "decisiones": ["Resumen", "Inventario proyectado", "Tú decides", "Todos los productos", "Detalle técnico"],
    "finanzas": ["Resumen", "Meta e ingresos", "Comparar productos", "Días fuertes", "Insumos", "Precio y costo"],
    "escenarios": ["Demanda", "Inventario", "Impacto en dinero"],
}


def opciones_vista(pagina):
    """Vistas disponibles de una página según el dataset activo."""
    dp, res = resultado()
    if dp is None:
        return []
    if pagina == "analisis":
        n = dp.df["entidad"].nunique()
        ops = ["Estacionalidad"] + (["Ranking ABC"] if n > 1 else [])
        if dp.tiene("promocion"):
            ops.append("Efecto de las promociones")
        if dp.tiene("quiebre") or dp.tiene("inventario"):
            ops.append("Quiebres de stock")
        return ops + (["Variabilidad"] if n > 1 else [])
    if res is None:
        return []
    ops = list(VISTAS.get(pagina, []))
    if dp.df["entidad"].nunique() == 1:
        ops = [o for o in ops if o not in ("Todos los productos", "Comparar productos")]
    if pagina == "finanzas" and dp.config.frecuencia != "D":
        ops = ["Meses fuertes" if o == "Días fuertes" else o for o in ops]
    return ops


def vista(pagina):
    """Vista elegida en el menú lateral para una página."""
    opciones = opciones_vista(pagina)
    v = st.session_state.get(f"vista_{pagina}")
    return v if v in opciones else (opciones[0] if opciones else None)


def menu_vistas(pagina):
    """Opciones de la página abierta, bajo su enlace del menú. La elección se recuerda al cambiar de página."""
    opciones = opciones_vista(pagina)
    if not opciones:
        return
    kw, estado = f"_menu_{pagina}", f"vista_{pagina}"
    st.session_state[estado] = vista(pagina)
    st.session_state[kw] = st.session_state[estado]

    def _sync():
        st.session_state[estado] = st.session_state[kw]

    with st.container(key=f"vistas_{pagina}"):
        st.radio("Qué ver", opciones, key=kw, on_change=_sync, label_visibility="collapsed",
                 format_func=etiqueta_vista)


MENU_CARGAR, MENU_ACTUALIZAR = "Información del dataset", "Actualización en tiempo real"


def menu_tus_datos(actual, p_datos, p_act, p_an, alertas=0):
    """Submenú de 1. Datos: información del dataset, actualización en tiempo real y los análisis del historial."""
    import cuenta
    destino = {MENU_CARGAR: p_datos}
    if cuenta.login_disponible():
        destino[MENU_ACTUALIZAR] = p_act
    opciones = list(destino) + opciones_vista("analisis")
    if actual.url_path == p_an.url_path:
        sel = vista("analisis")
    elif actual.url_path == p_act.url_path:
        sel = MENU_ACTUALIZAR
    else:
        sel = MENU_CARGAR
    kw = "_menu_tus_datos"
    st.session_state[kw] = sel if sel in opciones else MENU_CARGAR

    def _ir():
        v = st.session_state[kw]
        if v not in destino:
            st.session_state["vista_analisis"] = v
        st.session_state["_ir_menu"] = v

    with st.container(key="vistas_datos" + ("_alerta" if alertas and MENU_ACTUALIZAR in destino else "")):
        st.radio("Datos", opciones, key=kw, on_change=_ir, label_visibility="collapsed")
    ir = st.session_state.pop("_ir_menu", None)
    if ir:
        p = destino.get(ir, p_an)
        if p.url_path != actual.url_path:
            st.switch_page(p)


# ---------------------------------------------------------------- valores ($) por producto
# Precio y costo unitario: del archivo o asignados por el usuario. Sin valores se trabaja en unidades.

def _ultimo_valor(dp, rol, e):
    if not dp.tiene(rol):
        return None
    v = dp.df.loc[dp.df["entidad"] == e, rol].dropna()
    return float(v.iloc[-1]) if len(v) and v.iloc[-1] > 0 else None


def valores(dp=None) -> pd.DataFrame:
    """Tabla por producto: precio, costo y de dónde salió cada uno (archivo / tú / —)."""
    dp = dp or st.session_state.get("dp")
    if dp is None:
        return pd.DataFrame(columns=["precio", "costo", "origen_precio", "origen_costo"])
    usuario = st.session_state.get("valores_usuario", {}).get(clave_dataset(dp), {})
    filas = []
    for e in sorted(dp.df["entidad"].unique()):
        p_arch, c_arch = _ultimo_valor(dp, "precio", e), _ultimo_valor(dp, "costo_unitario", e)
        p_u, c_u = usuario.get("precio", {}).get(e), usuario.get("costo", {}).get(e)
        precio = p_u if p_u is not None else p_arch
        costo = c_u if c_u is not None else c_arch
        filas.append(dict(entidad=e, precio=precio or None, costo=costo or None,
                          origen_precio="tú" if p_u is not None else ("archivo" if p_arch else "—"),
                          origen_costo="tú" if c_u is not None else ("archivo" if c_arch else "—")))
    return pd.DataFrame(filas).set_index("entidad")


def guardar_valores(dp, tabla: pd.DataFrame):
    """Guarda lo que el usuario asignó (solo lo que difiere del archivo)."""
    reg = st.session_state.setdefault("valores_usuario", {}).setdefault(clave_dataset(dp), {"precio": {}, "costo": {}})
    for e, fila in tabla.iterrows():
        for col in ("precio", "costo"):
            v = fila[col]
            arch = _ultimo_valor(dp, "precio" if col == "precio" else "costo_unitario", e)
            v = None if v is None or pd.isna(v) or v <= 0 else float(v)
            if v is None and arch is None or (v is not None and arch is not None and abs(v - arch) < 1e-9):
                reg[col].pop(e, None)
            else:
                reg[col][e] = v if v is not None else 0.0   # 0 = el usuario borró el valor del archivo


def precio(e):
    v = valores()
    return float(v.loc[e, "precio"]) if e in v.index and pd.notna(v.loc[e, "precio"]) else None


def costo(e):
    """Costo unitario usable para margen: se ignora si es mayor o igual al precio (suele ser el precio de un insumo)."""
    v = valores()
    if e not in v.index or pd.isna(v.loc[e, "costo"]):
        return None
    c, p = float(v.loc[e, "costo"]), v.loc[e, "precio"]
    return None if (pd.notna(p) and c >= float(p)) else c


def costo_compra(e):
    """Costo por unidad comprada, tal como está (para gasto y capital en inventario)."""
    v = valores()
    return float(v.loc[e, "costo"]) if e in v.index and pd.notna(v.loc[e, "costo"]) else None


def hay_precios():
    v = valores()
    return bool(len(v)) and v["precio"].notna().any()


# ---------------------------------------------------------------- inflación (precios y costos hacia adelante)
INFLACION_DEFECTO = {"modo": "pais", "pais": "CHL", "fuente": "ipc12", "pct": 3.0}


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _inflacion_fmi(iso):
    from motor import inflacion as I
    return I.inflacion_fmi(iso)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _ipc_chile():
    from motor import inflacion as I
    return I.ipc_chile_12m()


def config_inflacion() -> dict:
    return {**INFLACION_DEFECTO, **(st.session_state.get("inflacion") or {})}


def guardar_config_inflacion(cfg: dict):
    if cfg == config_inflacion():
        return
    st.session_state["inflacion"] = dict(cfg)
    actual = st.session_state.get("config_actual")
    if actual is not None:
        actual["inflacion"] = dict(cfg)
        actualizar_registro(config=actual)


def _base_inflacion():
    dp = st.session_state.get("dp")
    return dp.df["fecha"].max() if dp is not None else pd.Timestamp.today().normalize()


def inflacion_actual() -> dict:
    """dict(tasas: % anual constante o {año: %}, texto, aviso, activa) según la configuración del usuario."""
    from motor import inflacion as I
    c = config_inflacion()
    if c["modo"] == "sin":
        return dict(tasas=0.0, texto="sin ajuste por inflación (precios de hoy)", aviso=None, activa=False,
                    corta="sin ajuste")
    if c["modo"] == "propio":
        return dict(tasas=float(c["pct"]), texto=f"{E.num(c['pct'], 1)}% anual (valor propio)", aviso=None,
                    activa=True, corta=f"{E.num(c['pct'], 1)}% anual")
    iso = c["pais"]
    nombre = I.PAISES.get(iso, iso)
    try:
        if iso == "CHL" and c["fuente"] == "ipc12":
            d = _ipc_chile()
            return dict(tasas=d["pct"], activa=True, aviso=None, corta=f"Chile · IPC {E.num(d['pct'], 1)}%",
                        texto=f"Chile {E.num(d['pct'], 1)}% anual · IPC de los últimos 12 meses "
                              f"(INE y Banco Central, a {d['hasta']:%m/%Y})")
        d = _inflacion_fmi(iso)
        base = _base_inflacion()
        dp = st.session_state.get("dp")
        dias = (horizonte() or 30) * (dp.freq_info["dias"] if dp is not None else 1)
        anos = list(range((base + pd.Timedelta(days=1)).year, (base + pd.Timedelta(days=dias)).year + 1))
        partes = " y ".join(f"{E.num(I.tasa_del_ano(d['tasas'], a), 1)}% en {a}" for a in anos)
        return dict(tasas=d["tasas"], activa=True, aviso=None,
                    corta=f"{nombre} · FMI {E.num(I.tasa_del_ano(d['tasas'], anos[0]), 1)}%",
                    texto=f"{nombre}: {partes} · proyección del FMI, {d['fuente']}")
    except Exception:  # noqa: BLE001
        fuente = "el IPC de Chile" if (iso == "CHL" and c["fuente"] == "ipc12") else "el FMI"
        return dict(tasas=float(c["pct"]), activa=True, corta=f"{E.num(c['pct'], 1)}% anual",
                    texto=f"{E.num(c['pct'], 1)}% anual",
                    aviso=f"No se pudo consultar {fuente} en este momento; se usa {E.num(c['pct'], 1)}% anual.")


def factor_inflacion(fechas) -> np.ndarray:
    """Multiplicador de precios y costos para cada fecha futura respecto del último dato del historial."""
    from motor import inflacion as I
    inf = inflacion_actual()
    if not inf["activa"]:
        return np.ones(len(fechas))
    return I.factores(fechas, _base_inflacion(), inf["tasas"])


def nota_inflacion() -> str:
    inf = inflacion_actual()
    if not inf["activa"]:
        return "Montos con precios de hoy, sin ajuste por inflación."
    return f"Precios y costos ajustados por inflación: {inf['texto']}. Se cambia en Finanzas → Precio y costo."


def costo_mantener_pct():
    """Costo anual de mantener inventario, como % de su valor (bodega, capital, mermas). 20% es una referencia común."""
    return float(st.session_state.get("costo_mantener_pct", 20.0))


def errores_modelo(e):
    """Errores reales del modelo (real − P50) en la prueba con datos pasados, para el stock de seguridad."""
    _, res = resultado()
    if res is None or e not in getattr(res, "backtest", {}):
        return None
    bt = res.backtest[e]
    return (bt["real"] - bt["P50"]).to_numpy(float)


def politica_actual(dp, entidades):
    """Parámetros de la política (lead time, inventario, nivel de servicio, revisión).

    Si el usuario ya los editó en Decisiones se usan esos; si no, los del archivo o valores por defecto.
    """
    from motor import politica as P
    guardada = st.session_state.get("politica")
    if guardada and guardada.get("clave") == clave_dataset(dp):
        return guardada["tabla"], guardada["nivel"], guardada["revision"]
    abierta = st.session_state.get("politica_guardada")
    if abierta and abierta.get("clave") == clave_dataset(dp) and abierta.get("tabla"):
        return pd.DataFrame(abierta["tabla"]), abierta.get("nivel", "90%"), abierta.get("revision", 30)
    filas = []
    for e in entidades:
        g = dp.df[dp.df["entidad"] == e]
        filas.append({
            "entidad": e,
            "lead_time": round(P.lead_time_dataset(g["lead_time"]), 1) if dp.tiene("lead_time") else 14.0,
            "inventario": float(g["inventario"].iloc[-1]) if dp.tiene("inventario") else float("nan"),
        })
    rev = {"D": 30, "W": 28, "M": 30, "Q": 91}[dp.config.frecuencia]
    return pd.DataFrame(filas), "90%", rev


# ---------------------------------------------------------------- UI compartida

_INGLES = {"store": "tienda", "shop": "tienda", "product": "producto", "item": "producto", "branch": "sucursal",
           "category": "categoría", "customer": "cliente", "client": "cliente", "warehouse": "bodega",
           "brand": "marca", "supplier": "proveedor", "location": "ubicación", "site": "local"}
_RELLENO = {"id", "cod", "codigo", "código", "nombre", "name", "nro", "num", "número", "numero", "de", "del", "code"}
_FEMENINOS = {"sucursal", "sede", "red", "clase", "serie", "base", "parte", "flor", "llave", "calle", "fuente"}
_MASCULINOS_EN_A = {"día", "dia", "mapa", "sistema", "problema", "clima", "programa", "tema", "idioma"}


def nombre_sugerido(et):
    """Nombre de cada serie deducido de la etiqueta de la columna de entidades: id_tienda -> tienda."""
    if et == "(una sola serie)" or not et:
        return "serie"
    palabras = [w for w in re.split(r"[\s_\-\.]+", str(et).strip()) if w]
    utiles = list(palabras)
    while len(utiles) > 1 and utiles[0].lower() in _RELLENO:
        utiles.pop(0)
    while len(utiles) > 1 and utiles[-1].lower() in _RELLENO:
        utiles.pop()
    et = " ".join(utiles)
    et = _INGLES.get(et.lower(), et)
    return et if et.isupper() else et.lower()


def _plural_palabra(w):
    if w.isupper():
        return w + "s"
    if w[-1] in "sx":
        return w
    if w[-1] in "aeiouáéó":
        return w + "s"
    if w.endswith("z"):
        return w[:-1] + "ces"
    if re.search(r"[áéíóú]n$", w):
        return w[:-2] + {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u"}[w[-2]] + "nes"
    return w + "es"


def nombre_entidad(dp, plural=False):
    """Cómo se llama cada serie (SKU, tienda, bebida…): lo que indicó el usuario o lo deducido de la columna."""
    alias = ((st.session_state.get("config_actual") or {}).get("nombre_serie") or "").strip()
    et = alias or nombre_sugerido(dp.etiquetas.get("entidad", ""))
    if not et.isupper():
        et = et[:1].lower() + et[1:]
    if not plural:
        return et
    partes = et.split(" ", 1)
    return " ".join([_plural_palabra(partes[0])] + partes[1:])


def es_femenino(dp):
    w = nombre_entidad(dp).split(" ")[0]
    if w.isupper() or w.lower() in _MASCULINOS_EN_A:
        return False
    return w.lower() in _FEMENINOS or w.endswith(("a", "ión", "dad", "tad", "tud", "umbre", "ie"))


def un_entidad(dp):
    return ("una " if es_femenino(dp) else "un ") + nombre_entidad(dp)


def todos_entidad(dp):
    return ("todas las " if es_femenino(dp) else "todos los ") + nombre_entidad(dp, True)


def etiqueta_vista(v, dp=None):
    """Nombre visible de una vista: las que hablan de los productos usan el nombre real de las series."""
    dp = dp or st.session_state.get("dp")
    if dp is None:
        return v
    if v == "Todos los productos":
        return mayus(todos_entidad(dp))
    if v == "Comparar productos":
        return "Comparar " + nombre_entidad(dp, True)
    return v


MAX_SERIES = 8


def mayus(texto):
    texto = str(texto)
    return texto[:1].upper() + texto[1:]


def _por_volumen(dp, entidades):
    vol = dp.df[dp.df["entidad"].isin(entidades)].groupby("entidad")["objetivo"].sum()
    return [e for e in vol.sort_values(ascending=False).index if e in entidades]


def _sync_vista(clave_widget):
    """Guarda la selección múltiple y deja como producto activo el último que se agregó."""
    antes = list(st.session_state.get("vista_sel", []))
    ahora = list(st.session_state.get(clave_widget) or [])
    st.session_state["vista_sel"] = ahora
    nuevos = [e for e in ahora if e not in antes]
    if nuevos:
        st.session_state["entidad"] = nuevos[-1]
    elif ahora and st.session_state.get("entidad") not in ahora:
        st.session_state["entidad"] = ahora[0]


def _activa_ultima(antes, ahora):
    nuevos = [e for e in ahora if e not in antes]
    if nuevos:
        st.session_state["entidad"] = nuevos[-1]
    elif ahora and st.session_state.get("entidad") not in ahora:
        st.session_state["entidad"] = ahora[0]


def _sync_puntos(opciones, estado, key, al_cambiar):
    antes = list(st.session_state.get(estado) or [])
    ahora = [o for i, o in enumerate(opciones) if st.session_state.get(f"_pm_{key}_{i}")]
    st.session_state[estado] = ahora
    if al_cambiar:
        al_cambiar(antes, ahora)


def _sync_all(opciones, estado, key, al_cambiar):
    """ALL marcado: todo seleccionado (se recuerda la selección previa). Desmarcado: vuelve la previa."""
    antes = list(st.session_state.get(estado) or [])
    if st.session_state.get(f"_pm_{key}_all"):
        st.session_state[f"_previa_{key}"] = antes
        ahora = list(opciones)
    else:
        previa = [o for o in st.session_state.get(f"_previa_{key}", []) if o in opciones]
        ahora = previa if previa and len(previa) < len(opciones) else list(opciones)[:1]
    st.session_state[estado] = ahora
    if al_cambiar:
        al_cambiar(antes, ahora)


def puntos_multi(opciones, estado, key, formato=None, maximo=None, al_cambiar=None, con_todos=False):
    """Selección múltiple con el mismo aspecto de la lista de puntos: cada punto se marca o desmarca solo.
    Con listas largas, el primer punto es ALL. La elección queda en session_state[estado] (lista)."""
    opciones = list(opciones)
    fmt = formato or str
    sel = [o for o in (st.session_state.get(estado) or []) if o in opciones]
    st.session_state[estado] = sel
    with st.container(key=f"puntos_{key}", gap=None):
        if con_todos and len(opciones) > 3:
            st.session_state[f"_pm_{key}_all"] = len(sel) == len(opciones)
            st.checkbox("ALL", key=f"_pm_{key}_all", on_change=_sync_all, args=(opciones, estado, key, al_cambiar))
        for i, o in enumerate(opciones):
            kw = f"_pm_{key}_{i}"
            st.session_state[kw] = o in sel
            st.checkbox(fmt(o), key=kw, on_change=_sync_puntos, args=(opciones, estado, key, al_cambiar),
                        disabled=maximo is not None and len(sel) >= maximo and o not in sel)
    return st.session_state[estado]


def selector_vista(entidades, dp, key, fila=None):
    """Selección de uno o varios productos (se superponen en los gráficos). Devuelve una lista; la elección se
    comparte entre páginas. Con `fila`, va como etiqueta compacta."""
    entidades = list(entidades)
    if len(entidades) <= 1:
        return entidades
    previa = [e for e in st.session_state.get("vista_sel", []) if e in entidades]
    activa = st.session_state.get("entidad")
    if activa in entidades and activa not in previa:
        previa = [activa]
    if not previa:
        previa = _por_volumen(dp, entidades)[:1]
    st.session_state["vista_sel"] = previa
    kw_sel = f"_vs_{key}"
    st.session_state[kw_sel] = previa
    etiqueta = f"{mayus(nombre_entidad(dp))}"
    if fila is not None:
        texto = f"ALL ({len(entidades)})" if len(previa) == len(entidades) else titulo_seleccion(previa, dp)
        with chip(fila, f"{etiqueta}: {texto}", f"vista_{key}"):
            puntos_multi(entidades, "vista_sel", key=f"vs_{key}", al_cambiar=_activa_ultima, con_todos=True)
        st.session_state[kw_sel] = st.session_state["vista_sel"]
    elif len(entidades) <= MAX_BOTONES:
        st.pills(etiqueta, entidades, selection_mode="multi", key=kw_sel, on_change=_sync_vista, args=(kw_sel,))
    else:
        st.multiselect(etiqueta, entidades, key=kw_sel, max_selections=MAX_SERIES, on_change=_sync_vista,
                       args=(kw_sel,), placeholder=f"Elige hasta {MAX_SERIES}")
    elegidas = [e for e in entidades if e in (st.session_state.get(kw_sel) or [])]
    if not elegidas:
        st.caption(f":material/info: Elige al menos {un_entidad(dp)}. Mientras tanto se muestra el primero.")
        elegidas = _por_volumen(dp, entidades)[:1]
    return elegidas


def titulo_seleccion(sel, dp):
    if len(sel) == 1:
        return str(sel[0])
    if len(sel) <= 3:
        return ", ".join(map(str, sel[:-1])) + " y " + str(sel[-1])
    return f"{len(sel)} {nombre_entidad(dp, True)}"


MAX_BOTONES = 12   # con más opciones que esto, los botones no caben y se usa una lista desplegable


def fila_chips(key):
    """Fila de etiquetas compactas (estándar de controles sobre cada gráfico)."""
    return st.container(horizontal=True, vertical_alignment="center", gap="small", key=f"fila_{key}")


def chip(fila, etiqueta, key, icono=None):
    """Etiqueta compacta que despliega su contenido: `with S.chip(fila, "Meta: $1M", "meta"): ...`."""
    with fila.container(key=f"chip_{key}", width="content"):
        return st.popover(etiqueta, width="content")


def info_pie(textos):
    """Datos informativos (no cambian el gráfico): una línea discreta bajo los gráficos."""
    textos = [t for t in textos if t]
    if textos:
        st.markdown('<div class="info-pie">' + " · ".join(textos) + "</div>", unsafe_allow_html=True)


def chip_texto(fila, texto, aviso=False):
    fila.markdown(f'<span class="chip-texto{" aviso" if aviso else ""}">{texto}</span>', unsafe_allow_html=True)


def chip_opcion(contenedor, prefijo, opciones, estado, key, icono=None, formato=None, defecto=None):
    """Etiqueta compacta que despliega las opciones (mismo estilo que el horizonte del pronóstico)."""
    opciones = list(opciones)
    if st.session_state.get(estado) not in opciones:
        st.session_state[estado] = defecto if defecto in opciones else opciones[0]
    kw = f"_chip_{key}"
    st.session_state[kw] = st.session_state[estado]
    fmt = formato or str

    def _sync():
        if st.session_state.get(kw) is not None:
            st.session_state[estado] = st.session_state[kw]

    with chip(contenedor, f"{prefijo}{fmt(st.session_state[estado])}", key, icono):
        st.radio(prefijo.strip(": ") or "Opción", opciones, key=kw, on_change=_sync, format_func=fmt,
                 label_visibility="collapsed")
    return st.session_state[estado]


def elegir_uno(etiqueta, opciones, key, estado, defecto=None, formato=None):
    """Selector de UNA opción como lista de puntos (estándar del sitio). `estado` es la clave de session_state
    donde se recuerda la elección."""
    opciones = list(opciones)
    if st.session_state.get(estado) not in opciones:
        st.session_state[estado] = defecto if defecto in opciones else opciones[0]
    if len(opciones) == 1:
        return opciones[0]
    kw = f"_uno_{key}"
    st.session_state[kw] = st.session_state[estado]

    def _sync_uno():
        v = st.session_state.get(kw)
        if v is not None:          # volver a tocar el botón activo no lo deja vacío
            st.session_state[estado] = v

    kwargs = dict(key=kw, on_change=_sync_uno)
    if formato:
        kwargs["format_func"] = formato
    st.radio(etiqueta, opciones, **kwargs)
    return st.session_state[estado]


def selector_entidad(entidades, dp, key="ent", fila=None):
    """Selector de una sola entidad. Con `fila`, va como etiqueta compacta. Comparte la elección con los
    selectores múltiples."""
    entidades = list(entidades)
    previa = [e for e in st.session_state.get("vista_sel", []) if e in entidades]
    defecto = previa[0] if previa else (_por_volumen(dp, entidades)[:1] or [None])[0]
    if fila is not None:
        if len(entidades) == 1:
            ent = entidades[0]
        else:
            ent = chip_opcion(fila, f"{mayus(nombre_entidad(dp))}: ", entidades, estado="entidad", key=f"ent_{key}",
                              defecto=defecto)
    else:
        ent = elegir_uno(mayus(nombre_entidad(dp)), entidades, key=key, estado="entidad", defecto=defecto)
    if ent not in st.session_state.get("vista_sel", []):
        st.session_state["vista_sel"] = [ent]
    return ent


def panel_dataset(compacto=True):
    dp = st.session_state.get("dp")
    if compacto:
        with st.sidebar:
            if dp is None:
                st.caption("Todavía no cargas un dataset.")
                return
            _, res = resultado()
            n = dp.df["entidad"].nunique()
            st.caption(f":material/description: {st.session_state.get('nombre_dataset', 'dataset')} · {n} "
                       f"{nombre_entidad(dp, n != 1)} · {dp.freq_info['nombre']}"
                       + (" · pronóstico listo" if res is not None else ""))
        return
    with st.sidebar:
        st.markdown("##### Tus datos")
        if dp is None:
            st.caption("Todavía no cargas un dataset.")
            return
        nombre = st.session_state.get("nombre_dataset", "dataset")
        _, res = resultado()
        estado = E.insignia("Pronóstico listo", "ok") if res is not None else E.insignia("Sin pronóstico", "neutro")
        n = dp.df["entidad"].nunique()
        st.markdown(
            f"""<div style="display:flex;flex-direction:column;gap:10px">
              <div class="ficha"><span class="l">Archivo</span><span class="v" style="word-break:break-all">{nombre}</span></div>
              <div style="display:flex;gap:18px">
                <div class="ficha"><span class="l">{mayus(nombre_entidad(dp, n != 1))}</span><span class="v">{E.num(n)}</span></div>
                <div class="ficha"><span class="l">Datos</span><span class="v">{dp.freq_info['nombre'].capitalize()}</span></div>
              </div>
              <div>{estado}</div>
            </div>""",
            unsafe_allow_html=True,
        )


def requiere_pronostico():
    """Detiene la página con un mensaje amable si todavía no hay pronóstico."""
    dp, res = resultado()
    if dp is None or res is None:
        panel_dataset()
        if dp is None:
            st.info("Primero carga tus datos en **Inicio** y genera el pronóstico.", icon=":material/info:")
            st.page_link("paginas/inicio.py", label="Ir a Inicio", icon=":material/arrow_forward:")
        else:
            st.info("Genera el pronóstico en **2. Pronóstico**.", icon=":material/info:")
            st.page_link("paginas/pronostico.py", label="Ir a 2. Pronóstico", icon=":material/arrow_forward:")
        st.stop()
    return dp, res


# ---------------------------------------------------------------- Mis pronósticos

def _usuario():
    import cuenta
    return cuenta.usuario()


def registro_actual():
    """Registro de Mis pronósticos del dataset activo (si hay sesión y ya se guardó)."""
    dp = st.session_state.get("dp")
    r = st.session_state.get("registro")
    if not r or dp is None or r["clave"] != clave_dataset(dp) or not _usuario():
        return None
    return r


def guardar_pronostico_actual(sin_modelo=False):
    """Crea el registro en Mis pronósticos al generar un pronóstico con sesión iniciada. Devuelve el error o None.
    sin_modelo=True: lo crea antes de entrenar (el entrenamiento corre en segundo plano)."""
    u = _usuario()
    dp, res = resultado()
    if not u or (res is None and not sin_modelo) or registro_actual():
        return None
    from motor import almacen as A
    from motor import repositorio as Rp
    nombre = st.session_state.get("nombre_dataset", "dataset")
    try:
        reg = repositorio().crear(u["correo"], dict(
            nombre=os.path.splitext(nombre)[0].replace("_", " ").capitalize(),
            archivo_nombre=nombre, clave_modelo=clave_dataset(dp), frecuencia=dp.config.frecuencia,
            n_entidades=int(dp.df["entidad"].nunique()), horizonte=int(horizonte() or 0),
            error_pct=None if res is None else (lambda v: None if pd.isna(v) else float(v))(
                res.metricas_entidad["wape"].mean()),
            config=st.session_state.get("config_actual", {}), escenarios=[],
        ))
        contenido = st.session_state.get("archivo_bytes")
        if contenido:
            almacen_persistente().escribir(A.ruta_datos(Rp.id_usuario(u["correo"]), reg["id"]), contenido)
        st.session_state["registro"] = {"id": reg["id"], "clave": clave_dataset(dp), "nombre": reg["nombre"],
                                        "escenarios": [], "vivo": config_vivo()}
        return None
    except Exception as e:  # noqa: BLE001
        return f"No se pudo guardar en Mis pronósticos: {type(e).__name__}: {e}"


def actualizar_registro(**cambios):
    """Actualiza el registro activo solo si algo cambió respecto de lo último guardado."""
    r = registro_actual()
    if not r:
        return
    import json
    firma = json.dumps(cambios, sort_keys=True, default=str)
    ultimas = st.session_state.setdefault("_firmas_registro", {})
    clave_firma = (r["id"], tuple(sorted(cambios)))
    if ultimas.get(clave_firma) == firma:
        return
    try:
        repositorio().actualizar(_usuario()["correo"], r["id"], cambios)
        ultimas[clave_firma] = firma
    except Exception as e:  # noqa: BLE001
        st.session_state.setdefault("avisos_almacen", []).append(f"No se pudo actualizar el registro: {e}")


def politica_a_json(tabla, nivel, revision):
    filas = []
    for f in tabla.to_dict("records"):
        filas.append({k: (None if isinstance(v, float) and v != v else v) for k, v in f.items()})
    return {"nivel": nivel, "revision": revision, "tabla": filas}


def abrir_pronostico(reg, progreso=None):
    """Deja la sesión como estaba al guardar: datos, columnas, modelo, horizonte, política y escenarios."""
    from motor import almacen as A
    from motor import repositorio as Rp
    u = _usuario()
    contenido = almacen_persistente().leer(A.ruta_datos(Rp.id_usuario(u["correo"]), reg["id"]))
    if contenido is None:
        raise FileNotFoundError("No se encontró el archivo de datos de este pronóstico.")
    nombre = reg["archivo_nombre"]
    df = leer(nombre, contenido)
    st.session_state.update(df_raw=df, nombre_dataset=nombre, archivo_bytes=contenido)

    cfg = reg.get("config") or {}
    roles = cfg.get("roles", {})
    k = f"{nombre}_{len(df)}"
    for rol, col in roles.items():            # la página Datos muestra las columnas tal como se guardaron
        st.session_state[f"rol_{rol}_{k}"] = col if col else "(no tiene)"
    for campo, llave in (("exogenas", "exog"), ("frecuencia", "freq"), ("relleno", "relleno"), ("negativos", "neg")):
        if campo in cfg:
            st.session_state[f"{llave}_{k}"] = cfg[campo]
    st.session_state[f"serie_{k}"] = cfg.get("nombre_serie") or ""
    st.session_state["_datos_preset"] = k

    st.session_state[f"clima_{k}"] = cfg.get("clima")
    dp = preparar_dataset(df, roles, cfg.get("exogenas", []), cfg.get("frecuencia", "D"),
                          cfg.get("relleno", "interpolar"), cfg.get("negativos", True),
                          cfg.get("suavizar_picos", SUAVIZAR_PICOS_DEFECTO), lugar=cfg.get("clima"))
    st.session_state["dp"] = dp
    st.session_state["config_actual"] = cfg
    if cfg.get("inflacion"):
        st.session_state["inflacion"] = dict(cfg["inflacion"])
    clave = clave_dataset(dp)
    plan = R.planificar(dp)
    res, origen = entrenar(clave, dp, plan, progreso)
    st.session_state["resultado"] = {"clave": clave, "res": res, "origen": origen}
    if reg.get("clave_modelo") != clave:     # los datos cambiaron (datos en vivo): el registro apunta al modelo nuevo
        try:
            repositorio().actualizar(u["correo"], reg["id"], dict(
                clave_modelo=clave, n_entidades=int(dp.df["entidad"].nunique()),
                error_pct=(lambda v: None if pd.isna(v) else float(v))(res.metricas_entidad["wape"].mean())))
        except Exception:  # noqa: BLE001
            pass
    h = int(reg.get("horizonte") or plan.horizonte_defecto)
    st.session_state["horizonte"] = h
    st.session_state[f"h_{clave}"] = min(h, plan.horizonte_max)
    st.session_state["registro"] = {"id": reg["id"], "clave": clave, "nombre": reg["nombre"],
                                    "escenarios": list(reg.get("escenarios") or []), "vivo": config_vivo(cfg)}
    st.session_state.pop("vivo_estado", None)
    st.session_state.pop("link_origen", None)
    pol = reg.get("politica")
    st.session_state["politica_guardada"] = {"clave": clave, **pol} if pol else None
    st.session_state.pop("politica", None)
    st.session_state.pop("escenario", None)
    return origen


# ---------------------------------------------------------------- datos en vivo

VIVO_DEFECTO = {"modo": "auto", "cada": "pedido"}
MODOS = {"auto": "Automático", "semana": "Una vez por semana", "pedido": "Manual"}
REVISAR_CADA_SEG = 60


def config_vivo(cfg=None) -> dict:
    """Opciones de datos en vivo de un análisis: link, si el link es el origen completo, modo y última actualización."""
    cfg = cfg if cfg is not None else (st.session_state.get("config_actual") or {})
    v = dict(cfg.get("vivo") or {})
    if cfg.get("vivo_link") and not v.get("link"):          # formato anterior
        v["link"] = cfg["vivo_link"]
    return {**VIVO_DEFECTO, **v}


def guardar_config_vivo(cambios: dict):
    """Guarda opciones de datos en vivo en el análisis abierto (sesión y registro)."""
    r = registro_actual()
    cfg = dict(st.session_state.get("config_actual") or {})
    v = {k: x for k, x in {**config_vivo(cfg), **cambios}.items() if x is not None}
    cfg["vivo"] = v
    cfg.pop("vivo_link", None)
    st.session_state["config_actual"] = cfg
    if r:
        r["vivo"] = v
        repositorio().actualizar(_usuario()["correo"], r["id"], {"config": cfg})
    st.session_state.pop("vivo_estado", None)


@st.cache_resource(show_spinner=False)
def _crear_vivo(huella):
    from motor import vivo as V
    return V.crear(dict(huella) if huella else None)


def almacen_vivo():
    """Claves de integración y filas en vivo: Supabase si hay secrets, si no SQLite local."""
    return _crear_vivo(_huella_config())


def url_api():
    try:
        return str(st.secrets["api"]["url"]).rstrip("/") if "api" in st.secrets else None
    except Exception:  # noqa: BLE001
        return None


@st.cache_data(ttl=REVISAR_CADA_SEG, show_spinner=False)
def descargar_link(url):
    from motor import vivo as V
    return V.descargar_link(url)


def nombre_desde_link(url, nombre_archivo):
    from urllib.parse import urlparse
    if "docs.google.com/spreadsheets" in url:
        return "planilla_google.csv"
    base = os.path.basename(urlparse(url).path) or "planilla"
    return base if base.lower().endswith((".csv", ".xlsx", ".xls")) else nombre_archivo


def huella_bytes(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()[:20]


def filas_del_link(url, roles):
    """Filas en vivo leídas del link (Google Sheets o CSV/Excel público). Lanza ValueError con un mensaje claro."""
    from motor import vivo as V
    nombre, contenido = descargar_link(url)
    try:
        df = leer(nombre, contenido)
    except D.ArchivoInvalido as e:
        raise ValueError(f"No se pudo leer la planilla del link: {e}") from None
    return V.filas_desde_tabla(df, roles)


def _msg_error_link(e):
    import requests
    if isinstance(e, ValueError):
        return str(e)
    if isinstance(e, (requests.ConnectionError, requests.Timeout)):
        return "No pudimos conectarnos con el link. Revisa que siga disponible e intenta de nuevo."
    return f"No se pudo leer la planilla ({type(e).__name__})."


def estado_vivo(refrescar=False):
    """Para el pronóstico abierto: datos nuevos sin incorporar e inventario recibido en vivo."""
    import time
    r = registro_actual()
    if not r:
        return None
    est = st.session_state.get("vivo_estado")
    if (est and est.get("v") == 2 and est["registro"] == r["id"] and est["clave"] == r["clave"] and not refrescar
            and time.time() - est["t"] < REVISAR_CADA_SEG):
        return est
    from motor import vivo as V
    cfg = st.session_state.get("config_actual") or {}
    vivo = config_vivo(cfg)
    roles, dp = cfg.get("roles") or {}, st.session_state["dp"]
    est = dict(v=2, t=time.time(), registro=r["id"], clave=r["clave"], pendientes=0, periodos=0, series=0, hasta=None,
               inventario={}, error_link=None, error=None, planilla_cambio=False)
    filas = []
    if vivo.get("link"):
        try:
            nombre, contenido = descargar_link(vivo["link"])
            if vivo.get("link_base"):
                est["planilla_cambio"] = huella_bytes(contenido) != vivo.get("hash")
            filas += V.filas_desde_tabla(leer(nombre, contenido), roles)
        except Exception as e:  # noqa: BLE001
            est["error_link"] = _msg_error_link(e)
    try:
        filas += almacen_vivo().leer_filas(_usuario()["correo"], r["id"])
        pend = V.cambios_pendientes(st.session_state["df_raw"], roles, filas, dp.config.frecuencia)
        est.update(pendientes=pend["n"], periodos=pend["periodos"], series=pend["series"], hasta=pend["hasta"],
                   inventario=V.inventario_reciente(filas, bool(roles.get("entidad"))))
    except Exception as e:  # noqa: BLE001
        est["error"] = f"No se pudieron revisar los datos nuevos: {type(e).__name__}"
    st.session_state["vivo_estado"] = est
    return est


def hay_pendientes(est) -> bool:
    return bool(est and (est["pendientes"] or est["planilla_cambio"]))


def inventario_vivo(dp):
    """{entidad: (inventario, fecha)} recibido en vivo para el pronóstico abierto."""
    est = estado_vivo()
    if not est:
        return {}
    return {e: v for e, v in est["inventario"].items() if e in set(dp.df["entidad"].unique())}


def toca_actualizar_solo(vivo) -> bool:
    """Según el modo elegido: siempre, una vez al día / semana, o nunca (solo a pedido)."""
    if vivo.get("modo", "auto") == "auto":
        return True
    dias = {"dia": 1, "semana": 7}.get(vivo.get("cada"))
    if not dias:
        return False
    ultima = pd.Timestamp(vivo["ultima"]) if vivo.get("ultima") else None
    return ultima is None or pd.Timestamp.now(tz="UTC") - ultima >= pd.Timedelta(days=dias)


def alertas_datos() -> int:
    """Cantidad de avisos para el globo rojo del menú (0 si no hay nada que hacer)."""
    try:
        est = estado_vivo()
    except Exception:  # noqa: BLE001
        return 0
    if not est or trabajo_activo() is not None:
        return 0
    return int(hay_pendientes(est) or bool(est["error_link"]))


def actualizar_con_datos_vivos(reg, progreso=None):
    """Junta el archivo (o la planilla conectada) con los datos en vivo, lo guarda como nueva versión y reentrena."""
    from motor import almacen as A
    from motor import repositorio as Rp
    from motor import servicio as Sv
    u = _usuario()
    _, cfg, nombre, contenido = Sv.datos_actualizados(dict(reg, usuario=u["correo"]), almacen_persistente(),
                                                      almacen_vivo(), descargar_link)
    if contenido is not None:
        almacen_persistente().escribir(A.ruta_datos(Rp.id_usuario(u["correo"]), reg["id"]), contenido)
    repositorio().actualizar(u["correo"], reg["id"], {"archivo_nombre": nombre, "config": cfg})
    return abrir_pronostico(dict(reg, archivo_nombre=nombre, config=cfg), progreso)


def actualizar_ahora(contenedor=None):
    """Actualiza el análisis abierto (en segundo plano si GitHub está configurado). Devuelve el error o None."""
    r = registro_actual()
    if not r:
        return "No hay un análisis guardado abierto."
    if segundo_plano_disponible():
        return encolar(r["id"], "pedido")
    reg = repositorio().obtener(_usuario()["correo"], r["id"])
    caja = contenedor or st
    barra = caja.progress(0.0, text="Juntando los datos nuevos…")
    try:
        actualizar_con_datos_vivos(reg, lambda f, t: barra.progress(min(f, 1.0), text=t))
        barra.empty()
        return None
    except Exception as e:  # noqa: BLE001
        barra.empty()
        return f"No se pudo actualizar: {_msg_error_link(e) if isinstance(e, ValueError) else e}"


def actualizacion_automatica():
    """Si llegaron datos y el modo lo permite, actualiza antes de mostrar la página (una vez por cambio)."""
    est = estado_vivo()
    if not hay_pendientes(est) or est.get("error_link") or not toca_actualizar_solo(config_vivo()):
        return
    firma = (est["registro"], est["clave"])
    if firma in (st.session_state.get("_auto_fallo"), st.session_state.get("_auto_hecho")):
        return      # ya se intentó con estos datos: no repetir
    if segundo_plano_disponible():
        if trabajo_activo() is None:
            err = encolar(est["registro"], "auto")
            st.session_state["_auto_fallo" if err else "_auto_hecho"] = firma
            st.session_state["_auto_error"] = err
        return
    with st.container(border=True):
        st.markdown(":material/sync: **Llegaron datos nuevos.** Actualizando tu pronóstico; no cierres esta pestaña.")
        err = actualizar_ahora()
    st.session_state["_auto_error"] = err
    if err:
        st.session_state["_auto_fallo"] = firma
    else:
        r = registro_actual()
        st.session_state["_auto_hecho"] = (r["id"], r["clave"]) if r else None
    st.rerun()


def texto_pendientes(est, dp) -> str:
    if not est["pendientes"]:
        return "La planilla conectada cambió." if est["planilla_cambio"] else ""
    fi = dp.freq_info
    p, ns = est["periodos"], est["series"]
    hasta = pd.Timestamp(est["hasta"]).strftime("%d/%m/%Y") if est["hasta"] is not None else ""
    en = f" en {E.num(ns)} {nombre_entidad(dp, ns != 1)}" if dp.df["entidad"].nunique() > 1 else ""
    return (f"{E.num(p)} {fi['unidad'] if p == 1 else fi['unidad_pl']} de ventas nuevas{en}"
            + (f" (hasta el {hasta})" if hasta else "") + ".")


def aviso_datos_nuevos(clave_boton="vivo_actualizar"):
    """Aviso con botón cuando llegaron ventas nuevas y la actualización es a pedido."""
    est = estado_vivo()
    if not hay_pendientes(est) or toca_actualizar_solo(config_vivo()) or trabajo_activo() is not None:
        return
    dp = st.session_state["dp"]
    with st.container(border=True):
        c1, c2 = st.columns([3, 1.2], vertical_alignment="center")
        c1.markdown(f":material/sync: **Hay datos nuevos**: {texto_pendientes(est, dp)} Actualiza para que el "
                    "pronóstico los use.")
        if c2.button("Actualizar pronóstico", key=clave_boton, type="primary", icon=":material/refresh:",
                     width="stretch"):
            err = actualizar_ahora()
            if err:
                st.error(err)
            else:
                st.rerun()


# ---------------------------------------------------------------- entrenamiento en segundo plano (GitHub Actions)

def config_github():
    try:
        g = dict(st.secrets["github"]) if "github" in st.secrets else None
    except Exception:  # noqa: BLE001
        return None
    return g if g and g.get("token") and g.get("repo") else None


def segundo_plano_disponible() -> bool:
    """Hay GitHub configurado y sesión iniciada (los trabajos se guardan a nombre del usuario)."""
    return bool(config_github() and _usuario())


@st.cache_resource(show_spinner=False)
def _crear_cola(huella):
    from motor import trabajos as T
    return T.crear(dict(huella) if huella else None)


def cola():
    return _crear_cola(_huella_config())


def encolar(registro_id, origen="pedido"):
    """Anota el trabajo y avisa a GitHub. Devuelve el error o None."""
    from motor import trabajos as T
    g = config_github()
    try:
        t = cola().crear(_usuario()["correo"], registro_id, origen)
    except Exception as e:  # noqa: BLE001
        return f"No se pudo anotar el entrenamiento: {type(e).__name__}. ¿Corriste el SQL de trabajos en Supabase?"
    try:
        T.disparar_github(g["token"], g["repo"], t["id"], g.get("workflow", "entrenar.yml"), g.get("rama", "main"))
    except Exception as e:  # noqa: BLE001
        cola().actualizar(t["id"], estado="error", mensaje=str(e)[:300], terminado=T.ahora())
        return f"No se pudo iniciar el entrenamiento en segundo plano: {e}"
    st.session_state["trabajo"] = {"id": t["id"], "registro": registro_id}
    st.session_state.pop("_auto_error", None)
    return None


def trabajo_activo():
    """Trabajo en curso del análisis abierto (o del que se está generando), o None."""
    r = registro_actual() or st.session_state.get("registro")
    reg_id = (st.session_state.get("trabajo") or {}).get("registro") or (r or {}).get("id")
    if not reg_id or not segundo_plano_disponible():
        return None
    try:
        t = cola().ultimo(reg_id)
    except Exception:  # noqa: BLE001
        return None
    if t and t["estado"] in ("pendiente", "corriendo"):
        return t
    seguido = st.session_state.get("trabajo")
    if t and seguido and seguido["id"] == t["id"]:
        return t                      # recién terminó: el panel lo muestra y recarga el análisis
    return None


@st.fragment(run_every=5)
def panel_trabajo():
    """Estado del entrenamiento en segundo plano; al terminar, carga el modelo nuevo."""
    t = trabajo_activo()
    if t is None:
        return
    if t["estado"] == "listo":
        st.session_state.pop("trabajo", None)
        reg = repositorio().obtener(_usuario()["correo"], t["registro_id"])
        if reg:
            with st.spinner("Cargando el pronóstico actualizado…"):
                abrir_pronostico(reg)
        st.session_state["_aviso_listo"] = True
        st.rerun(scope="app")
    if t["estado"] == "cancelado":
        st.session_state.pop("trabajo", None)
        return
    if t["estado"] == "error":
        st.session_state.pop("trabajo", None)
        st.error(f"El entrenamiento en segundo plano falló: {t.get('mensaje') or 'sin detalle'}",
                 icon=":material/error:")
        return
    from motor import trabajos as T
    if "_estimado" not in st.session_state or st.session_state["_estimado"][0] != t["id"]:
        st.session_state["_estimado"] = (t["id"], T.duracion_estimada(cola(), t["registro_id"]))
    total = st.session_state["_estimado"][1]
    lleva = max(0.0, (pd.Timestamp.now(tz="UTC") - pd.Timestamp(t["creado"])).total_seconds())
    falta = total - lleva
    reloj = f"{int(lleva // 60)}:{int(lleva % 60):02d}"
    resto = (f"faltan unos {max(1, round(falta / 60))} min" if falta > 45 else "casi listo")
    with st.container(border=True):
        c1, c2 = st.columns([2, 1.5], vertical_alignment="center")
        accion = "Entrenando" if t.get("origen") == "nuevo" else "Actualizando"
        c1.markdown(f":material/cloud_sync: **{accion} tu pronóstico en segundo plano** · {reloj} · {resto}")
        c2.progress(min(0.97, max(float(t.get("progreso") or 0.0), lleva / total if total else 0.0)))


def detener_automatico():
    """Al apagar la actualización automática se detiene la que esté en curso (no las pedidas a mano)."""
    from motor import trabajos as T
    t = trabajo_activo()
    if t and t.get("origen") == "auto" and t["estado"] in T.ACTIVOS:
        try:
            cola().actualizar(t["id"], estado="cancelado", terminado=T.ahora(), mensaje="Detenido por el usuario")
        except Exception:  # noqa: BLE001
            return
        st.session_state.pop("trabajo", None)


def aviso_listo():
    if st.session_state.pop("_aviso_listo", False):
        st.toast("Pronóstico actualizado con los datos nuevos.", icon=":material/check_circle:")

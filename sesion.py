"""Estado compartido entre páginas: dataset activo, modelo entrenado, pronóstico y modo desarrollador."""

import hashlib
import os

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


@st.cache_data(show_spinner="Preparando los datos...")
def preparar_cacheado(df, roles_items, exogenas, frecuencia, relleno, negativos):
    cfg = D.Configuracion(roles=dict(roles_items), exogenas=list(exogenas), frecuencia=frecuencia,
                          relleno_objetivo=relleno, negativos_a_cero=negativos)
    return D.preparar(df, cfg)


def clave_dataset(dp) -> str:
    h = hashlib.sha256(pd.util.hash_pandas_object(dp.df, index=False).values.tobytes())
    h.update(str(dp.variables_modelo).encode())
    h.update(dp.config.frecuencia.encode())
    return h.hexdigest()[:16]


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


@st.cache_data(show_spinner="Calculando el pronóstico...", max_entries=20)
def _pronostico_cacheado(clave, h, freq):
    res = st.session_state["resultado"]["res"]
    from motor import modelo as M
    return M.pronosticar(res, h, freq)


def pronostico(h=None):
    dp, res = resultado()
    if res is None:
        return None
    return _pronostico_cacheado(st.session_state["resultado"]["clave"], h or horizonte(), dp.config.frecuencia)


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
    return _pronostico_escenario_cacheado(st.session_state["resultado"]["clave"], h, dp.config.frecuencia,
                                          cambios_t, tuple(entidades))


# ---------------------------------------------------------------- valores ($) por producto
# Precio de venta y costo unitario: se toman del archivo cuando vienen y el usuario puede asignarlos o corregirlos.
# Sin valores, el sitio muestra todo en unidades: nunca inventa montos.

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

def nombre_entidad(dp, plural=False):
    et = dp.etiquetas.get("entidad", "")
    if et == "(una sola serie)" or not et:
        return "series" if plural else "serie"
    et = et.strip()
    for sufijo in (" ID", " id", " código", " Código", " cod"):
        if et.endswith(sufijo) and len(et) > len(sufijo):
            et = et[: -len(sufijo)]
    et = et if et.isupper() else et.lower()
    if plural:
        if et.isupper():
            return et + "s"
        return et if et.endswith("s") else et + ("es" if et[-1] in "rlnd" else "s")
    return et


MAX_SERIES = 8


def mayus(texto):
    texto = str(texto)
    return texto[:1].upper() + texto[1:]


def _por_volumen(dp, entidades):
    vol = dp.df[dp.df["entidad"].isin(entidades)].groupby("entidad")["objetivo"].sum()
    return [e for e in vol.sort_values(ascending=False).index if e in entidades]


def _sync(clave_widget, clave_estado):
    st.session_state[clave_estado] = st.session_state[clave_widget]


def selector_vista(entidades, dp, key):
    """Selector visible sobre el gráfico: total o uno/varios/todos los productos.
    Devuelve None para la vista total o la lista de entidades elegidas. La elección se comparte entre páginas."""
    entidades = list(entidades)
    if len(entidades) <= 1:
        return None
    nom = nombre_entidad(dp)
    modos = ["Total", f"Por {nom}"]
    if st.session_state.get("vista_modo") not in ("Total", "Por"):
        st.session_state["vista_modo"] = "Total"
    previa = [e for e in st.session_state.get("vista_sel", []) if e in entidades]
    if not previa:
        previa = entidades if len(entidades) <= MAX_SERIES else _por_volumen(dp, entidades)[:5]
    st.session_state["vista_sel"] = previa

    kw_modo, kw_sel = f"_vm_{key}", f"_vs_{key}"
    st.session_state[kw_modo] = modos[0] if st.session_state["vista_modo"] == "Total" else modos[1]
    st.session_state[kw_sel] = previa

    def _sync_modo():
        v = st.session_state.get(kw_modo)
        st.session_state["vista_modo"] = "Total" if v in (None, "Total") else "Por"

    c1, c2 = st.columns([1, 3], vertical_alignment="bottom")
    with c1:
        st.segmented_control("Ver", modos, key=kw_modo, on_change=_sync_modo, width="stretch")
    if st.session_state["vista_modo"] == "Total":
        c2.caption(f"Suma de {'los' if not nom.endswith('a') else 'las'} {len(entidades)} {nombre_entidad(dp, True)}. "
                   f"Elige «{modos[1]}» para verlos por separado o compararlos.")
        return None
    with c2:
        etiqueta = f"{mayus(nombre_entidad(dp, True))} a mostrar"
        if len(entidades) <= 12:
            elegidas = st.pills(etiqueta, entidades, selection_mode="multi", key=kw_sel,
                                on_change=_sync, args=(kw_sel, "vista_sel"))
        else:
            elegidas = st.multiselect(etiqueta, entidades, key=kw_sel, max_selections=MAX_SERIES,
                                      on_change=_sync, args=(kw_sel, "vista_sel"),
                                      placeholder=f"Elige hasta {MAX_SERIES}")
    elegidas = [e for e in entidades if e in (elegidas or [])]
    if not elegidas:
        st.caption(f":material/info: Elige al menos un {nom}. Mientras tanto se muestra el total.")
        return None
    return elegidas


def titulo_seleccion(sel, dp):
    if len(sel) == 1:
        return str(sel[0])
    if len(sel) <= 3:
        return ", ".join(map(str, sel[:-1])) + " y " + str(sel[-1])
    return f"{len(sel)} {nombre_entidad(dp, True)}"


MAX_BOTONES = 12   # con más opciones que esto, los botones no caben y se usa una lista desplegable


def elegir_uno(etiqueta, opciones, key, estado, defecto=None, formato=None):
    """Selector de UNA opción con el mismo estilo en todo el sitio: botones (pills) si caben,
    lista desplegable si hay muchas. `estado` es la clave de session_state donde se recuerda la elección."""
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
    if len(opciones) <= MAX_BOTONES:
        st.pills(etiqueta, opciones, selection_mode="single", **kwargs)
    else:
        c1, _ = st.columns([1, 2])
        with c1:
            st.selectbox(etiqueta, opciones, **kwargs)
    return st.session_state[estado]


def selector_entidad(entidades, dp, key="ent"):
    """Selector de una sola entidad, visible en la página (no en la barra lateral)."""
    return elegir_uno(mayus(nombre_entidad(dp)), entidades, key=key, estado="entidad")


def panel_dataset():
    dp = st.session_state.get("dp")
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
        st.info("Primero carga tus datos y genera el pronóstico.", icon=":material/info:")
        st.page_link("paginas/datos.py", label="Ir a Datos", icon=":material/arrow_forward:")
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


def guardar_pronostico_actual():
    """Crea el registro en Mis pronósticos al generar un pronóstico con sesión iniciada. Devuelve el error o None."""
    u = _usuario()
    dp, res = resultado()
    if not u or res is None or registro_actual():
        return None
    from motor import almacen as A
    from motor import repositorio as Rp
    nombre = st.session_state.get("nombre_dataset", "dataset")
    try:
        reg = repositorio().crear(u["correo"], dict(
            nombre=os.path.splitext(nombre)[0].replace("_", " ").capitalize(),
            archivo_nombre=nombre, clave_modelo=clave_dataset(dp), frecuencia=dp.config.frecuencia,
            n_entidades=int(dp.df["entidad"].nunique()), horizonte=int(horizonte() or 0),
            error_pct=float(res.metricas_entidad["wape"].mean()),
            config=st.session_state.get("config_actual", {}), escenarios=[],
        ))
        contenido = st.session_state.get("archivo_bytes")
        if contenido:
            almacen_persistente().escribir(A.ruta_datos(Rp.id_usuario(u["correo"]), reg["id"]), contenido)
        st.session_state["registro"] = {"id": reg["id"], "clave": clave_dataset(dp), "nombre": reg["nombre"],
                                        "escenarios": []}
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

    dp = preparar_cacheado(df, tuple(sorted(roles.items())), tuple(cfg.get("exogenas", [])), cfg.get("frecuencia", "D"),
                           cfg.get("relleno", "interpolar"), cfg.get("negativos", True))
    st.session_state["dp"] = dp
    st.session_state["config_actual"] = cfg
    clave = clave_dataset(dp)
    plan = R.planificar(dp)
    res, origen = entrenar(clave, dp, plan, progreso)
    st.session_state["resultado"] = {"clave": clave, "res": res, "origen": origen}
    h = int(reg.get("horizonte") or plan.horizonte_defecto)
    st.session_state["horizonte"] = h
    st.session_state[f"h_{clave}"] = min(h, plan.horizonte_max)
    st.session_state["registro"] = {"id": reg["id"], "clave": clave, "nombre": reg["nombre"],
                                    "escenarios": list(reg.get("escenarios") or [])}
    pol = reg.get("politica")
    st.session_state["politica_guardada"] = {"clave": clave, **pol} if pol else None
    st.session_state.pop("politica", None)
    st.session_state.pop("escenario", None)
    return origen

import io

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import estilo as E
import sesion as S
from motor import escenarios as X
from motor import politica as P
from motor.datos import FRECUENCIAS

dp, res = S.requiere_pronostico()
S.panel_dataset()
fi = dp.freq_info
freq = dp.config.frecuencia
H = S.horizonte()
nom = S.mayus(S.nombre_entidad(dp))
entidades = list(res.series)
_activa = st.session_state.get("entidad")
if (_activa in entidades and _activa != st.session_state.get("_esc_activa")
        and st.session_state.get("esc_alcance") not in (None, "Todas")):
    st.session_state["esc_alcance"] = _activa
u, u_pl = fi["unidad"], fi["unidad_pl"]


def ultimo(rol, e):
    """Último valor conocido de una columna (precio, costo) para una entidad."""
    if not dp.tiene(rol):
        return None
    v = dp.df.loc[dp.df["entidad"] == e, rol].dropna()
    return float(v.iloc[-1]) if len(v) else None


def costo_alternativo_detectado(e):
    """Si el archivo trae el costo de un proveedor secundario/alternativo, lo usa como sugerencia."""
    import re
    raw = st.session_state.get("df_raw")
    col_ent = (st.session_state.get("config_actual") or {}).get("roles", {}).get("entidad")
    if raw is None:
        return None
    for c in raw.columns:
        n = str(c).lower()
        if re.search(r"secundari|alternativ|backup|respaldo", n) and re.search(r"precio|costo|cost|price", n) \
                and pd.api.types.is_numeric_dtype(raw[c]):
            v = raw.loc[raw[col_ent].astype(str).str.strip() == e, c] if col_ent and col_ent in raw else raw[c]
            v = v.dropna()
            if len(v):
                return float(v.iloc[-1])
    return None

vista_pag = S.vista("escenarios")
E.titulo_compacto("Escenarios", S.etiqueta_vista(vista_pag))

import cuenta  # noqa: E402

registro = S.registro_actual()
guardados = registro.get("escenarios", []) if registro else []

# ---------------------------------------------------------------- armar el escenario
disponibles = ["demanda", "retraso"]
if "precio" in res.exog_nombres:
    disponibles.append("precio")
if "promocion" in res.exog_nombres:
    disponibles.append("promocion")
efecto_clima = S.clima_efecto()
otras = [v for v in res.exog_nombres if v not in ("precio", "promocion")]
if efecto_clima is not None and len(efecto_clima):
    otras += [v for v in X.VARIABLES_CLIMA
              if v not in otras and efecto_clima.loc[efecto_clima["variable"] == v, "relevante"].any()]
if otras:
    disponibles.append("exogena")

@st.cache_data(show_spinner=False)
def _proximos_pedidos(clave, h, nivel, revision, tabla_json):
    tabla = pd.read_json(io.StringIO(tabla_json)).set_index("entidad")
    fut = S.pronostico(h)
    out = {}
    for e in fut:
        if e not in tabla.index or pd.isna(tabla.loc[e, "inventario"]):
            continue
        f = tabla.loc[e]
        par = P.Parametros(lead_time_dias=float(f["lead_time"]), revision_dias=float(revision), nivel_servicio=nivel,
                           inventario_actual=float(f["inventario"]), errores=S.errores_modelo(e))
        sim = P.simular(fut[e], P.decidir(e, fut[e], freq, par))
        ped = sim[sim["pedido"] > 0] if len(sim) else sim
        out[e] = pd.Timestamp(ped["fecha"].iloc[0]).isoformat() if len(ped) else None
    return out


def proximo_pedido(e=None):
    """Fecha del próximo pedido (de una entidad o el más cercano de todas) según la política actual."""
    try:
        tabla, nivel_, rev_ = S.politica_actual(dp, entidades)
        fechas = _proximos_pedidos(S.clave_dataset(dp), res.plan.horizonte_max, nivel_, rev_,
                                   tabla.to_json(orient="records", date_format="iso"))
    except Exception:  # noqa: BLE001
        return None
    vals = [fechas.get(e)] if e else list(fechas.values())
    vals = [pd.Timestamp(v) for v in vals if v]
    return min(vals) if vals else None


primera_fecha = res.series[entidades[0]].fechas[-1] + pd.tseries.frequencies.to_offset(FRECUENCIAS[freq]["pandas"])
dur_def = {"D": 30, "W": 6, "M": 3, "Q": 2}[freq]
dur_max = max(1, min(res.plan.horizonte_max - 1, {"D": 120, "W": 26, "M": 12, "Q": 4}[freq]))

ss = st.session_state
fila = S.fila_chips("esc")
alcance = S.chip_opcion(fila, "Afecta a: ", (["Todas"] if len(entidades) > 1 else []) + entidades, estado="esc_alcance",
                        key="esc_alc", formato=lambda x: "Todos" if x == "Todas" else x)
if alcance != "Todas":
    ss["entidad"] = alcance
    if alcance not in ss.get("vista_sel", []):
        ss["vista_sel"] = [alcance]
ss["_esc_activa"] = ss.get("entidad")

if "esc_tipos_sel" not in ss:
    ss["esc_tipos_sel"] = ["retraso"]
with S.chip(fila, "Eventos", "esc_tipos", icono=":material/add:"):
    tipos = S.puntos_multi(disponibles, "esc_tipos_sel", key="esc_tipos", formato=lambda t: X.TIPOS[t])
    st.caption("Puedes combinar varios.")
eventos = []
for t in tipos or []:
    if t == "demanda":
        with S.chip(fila, f"Demanda: {ss.get('esc_dem', 25):+d}%", "esc_dem"):
            v = st.slider("Cambio en la demanda (%)", -60, 150, 25, 5, key="esc_dem",
                          help="Por ejemplo, un cliente nuevo (+) o un competidor que entra (−).")
        eventos.append(X.Evento("demanda", v / 100))
    elif t == "retraso":
        con_alt = bool(ss.get("esc_alt")) and any(S.costo_compra(x) for x in entidades)
        with S.chip(fila, f"Retraso: +{ss.get('esc_ret', 10)} días" + (" · proveedor alternativo" if con_alt else ""),
                    "esc_ret"):
            v = st.number_input("Días adicionales de retraso", 1, 180, 10, key="esc_ret",
                                help="Los pedidos que emitas durante el evento llegan con este retraso extra.")
            c_alt = None
            if any(S.costo_compra(x) for x in entidades):
                cubrir = st.toggle("Evaluar cubrirlo con un proveedor alternativo", key="esc_alt",
                                   help="Compara quedarte con el retraso contra comprarle a otro proveedor que "
                                        "llega a tiempo pero cobra más por unidad.")
                if cubrir:
                    ref = alcance if alcance != "Todas" else entidades[0]
                    sug = costo_alternativo_detectado(ref) or (S.costo_compra(ref) or 0) * 1.3
                    c_alt = st.number_input("Costo por unidad del proveedor alternativo ($)", 0.0, None,
                                            float(round(sug)), 50.0, format="%.0f", key="esc_alt_costo",
                                            help=f"Hoy pagas ${E.num(S.costo_compra(ref) or 0)} por "
                                                 f"unidad a tu proveedor principal ({ref}).")
        eventos.append(X.Evento("retraso", float(v), costo_alt=c_alt))
    elif t == "precio":
        alc = alcance
        ref_p = S.precio(alc) if alc != "Todas" else None
        como_txt = ss.get("esc_pre_como", "%") if ref_p else "%"
        k_monto = f"esc_pre_monto_{alc}"
        etiqueta = (f"Precio: {ss.get('esc_pre', 15):+d}%" if como_txt == "%"
                    else f"Precio: {E.clp_corto(ss.get(k_monto, float(round((ref_p or 0) * 1.15))))}")
        with S.chip(fila, etiqueta, "esc_pre"):
            como = S.elegir_uno("Cambio de precio en", ["%", "Precio nuevo ($)"] if ref_p else ["%"],
                                key="pre_como", estado="esc_pre_como")
            if como == "%":
                v = st.slider("Cambio de precio (%)", -50, 50, 15, 5, key="esc_pre")
                if not ref_p and len(entidades) > 1:
                    st.caption("Para ingresarlo en pesos, elige " + S.un_entidad(dp) + " en «Afecta a».")
            else:
                nuevo = st.number_input(f"Precio nuevo de {alc} ($)", 1.0, None, float(round(ref_p * 1.15)),
                                        10.0, format="%.0f", key=k_monto)
                v = (nuevo / ref_p - 1) * 100
                st.caption(f"Hoy cuesta {E.clp_md(ref_p)}: es un cambio de " + f"{v:+.1f}".replace(".", ",") + "%.")
            usar_el = st.toggle("Usar una sensibilidad al precio conocida", key="esc_pre_usar_el",
                                help="Por defecto, el efecto del precio en la demanda lo calcula el modelo "
                                     "con lo que aprendió de tu historial.")
            el = st.number_input("Elasticidad precio-demanda", -3.0, 0.0, -0.3, 0.05, format="%.2f",
                                 key="esc_pre_el", disabled=not usar_el,
                                 help="Cuánto cambian tus ventas cuando cambia el precio. Por ejemplo, −0,3 significa "
                                      "que si el precio sube 10%, vendes 3% menos.")
        eventos.append(X.Evento("precio", v / 100, elasticidad=float(el) if usar_el else None))
    elif t == "promocion":
        with S.chip(fila, "Promoción", "esc_promo"):
            st.caption("Se activa una promoción durante todo el evento; el modelo estima su efecto.")
        eventos.append(X.Evento("promocion", 1.0))
    elif t == "exogena":
        var_txt = ss.get("esc_var") if ss.get("esc_var") in otras else otras[0]
        unidad_clima = {"temperatura": "°C", "lluvia": "mm por día"}.get(var_txt)
        k_val = f"esc_exo_{var_txt}" if unidad_clima else "esc_exo"
        defecto = {"temperatura": 3, "lluvia": 5}.get(var_txt, 30)
        etiqueta = (f"{ss.get(k_val, defecto):+d} {unidad_clima.split()[0]}" if unidad_clima
                    else f"{ss.get(k_val, defecto):+d}%")
        with S.chip(fila, f"{dp.etiquetas.get(var_txt, var_txt)}: {etiqueta}", "esc_exo"):
            var = S.elegir_uno("Variable", otras, key="var", estado="esc_var",
                               formato=lambda v: dp.etiquetas.get(v, v))
            if unidad_clima:
                v = st.slider(f"Cambio ({unidad_clima})", -10 if var_txt == "temperatura" else 0,
                              10 if var_txt == "temperatura" else 40, defecto, 1, key=k_val)
            else:
                v = st.slider("Cambio (%)", -60, 150, 30, 5, key=k_val)
        eventos.append(X.Evento("exogena", float(v) if unidad_clima else v / 100, var))

adelanto = {"D": pd.Timedelta(days=14), "W": pd.Timedelta(weeks=2), "M": pd.DateOffset(months=1), "Q": pd.DateOffset(months=3)}[freq]
# el retraso solo se nota si el evento incluye un pedido: se sugiere empezar en el próximo pedido
ref_ent = alcance if alcance != "Todas" else None
prox = proximo_pedido(ref_ent)
inicio_def = (primera_fecha + adelanto).date()
if prox is not None and "retraso" in (tipos or []) and prox.date() >= primera_fecha.date():
    inicio_def = prox.date()
with S.chip(fila, f"Empieza: {ss.get('esc_ini', inicio_def):%d/%m/%Y}", "esc_ini", icono=":material/event:"):
    fecha_ini = st.date_input("Empieza", inicio_def, min_value=primera_fecha.date(), format="DD/MM/YYYY", key="esc_ini")
    if prox is not None:
        st.caption(f"Próximo pedido{' de ' + ref_ent if ref_ent else ''}: **{prox:%d/%m/%Y}**. Un retraso solo se "
                   "nota si el evento incluye un pedido.")
dur_txt = ss.get("esc_dur", min(dur_def, dur_max))
with S.chip(fila, f"Dura: {dur_txt} {u_pl if dur_txt != 1 else u}", "esc_dur"):
    duracion = st.slider(f"Dura ({u_pl})", 1, dur_max, min(dur_def, dur_max), key="esc_dur")
offset = pd.tseries.frequencies.to_offset(FRECUENCIAS[freq]["pandas"])
desde = len(pd.date_range(primera_fecha, pd.Timestamp(fecha_ini), freq=offset)) - 1
actual = dict(clave=S.clave_dataset(dp), eventos=eventos, desde=max(0, desde), duracion=duracion, alcance=alcance)
ya_simulado = bool(eventos) and repr(st.session_state.get("escenario")) == repr(actual)
with fila.container(width="content"):
    if ya_simulado:
        simular = st.button("Simulado", icon=":material/check:", disabled=True, key="btn_simulado")
    else:
        simular = st.button("Simular", type="primary", icon=":material/play_arrow:", disabled=not eventos,
                            key="btn_simular")
if guardados:
    with S.chip(fila, "Guardados", "esc_guardados", icono=":material/bookmark:"):
        nombres = [g["nombre"] for g in guardados]
        elegido = st.radio("Tus escenarios guardados", nombres, index=None, key="esc_guardado_sel",
                           label_visibility="collapsed")
        if elegido and st.session_state.get("_esc_cargado") != elegido:
            g = guardados[nombres.index(elegido)]
            st.session_state["escenario"] = dict(clave=S.clave_dataset(dp), desde=g["desde"], duracion=g["duracion"],
                                                 alcance=g["alcance"] if g["alcance"] in (["Todas"] + list(res.series)) else "Todas",
                                                 eventos=[X.Evento(**ev) for ev in g["eventos"]])
            st.session_state["_esc_cargado"] = elegido

if simular:
    st.session_state.pop("_esc_cargado", None)
    st.session_state["escenario"] = actual
    st.rerun()

guardado = st.session_state.get("escenario")
if not guardado or guardado["clave"] != S.clave_dataset(dp):
    st.caption(":material/info: Elige uno o más eventos y presiona **Simular**.")
    st.stop()
if repr(guardado) != repr(actual):
    with st.container(key="esc_desactualizado"):
        st.caption(":material/refresh: " + ("Cambiaste el escenario: presiona **Simular** para actualizarlo"
                                            if eventos else "Elige al menos un evento para simular")
                   + "; mientras tanto ves el último que simulaste.")

esc = X.Escenario(guardado["eventos"], guardado["desde"], guardado["duracion"])
afectadas = entidades if guardado["alcance"] == "Todas" else [guardado["alcance"]]

# ---------------------------------------------------------------- cálculo
tabla_pol, nivel, revision = S.politica_actual(dp, entidades)
tabla_pol = tabla_pol.set_index("entidad")
lt_max = float(tabla_pol.loc[afectadas, "lead_time"].max()) + esc.retraso_dias()
necesarios = int(np.ceil((lt_max + revision) / fi["dias"]))
H_an = int(min(res.plan.horizonte_max, max(H, esc.hasta + 2 * necesarios)))
if esc.desde >= H_an:
    st.error(f"El evento empieza después del máximo que se puede pronosticar ({res.plan.horizonte_max} {u_pl}). "
             "Elige una fecha más cercana.")
    st.stop()

base = S.pronostico(H_an)
con_evento = S.pronostico_escenario(H_an, esc.cambios_modelo(), afectadas)
# el clima mueve la demanda según el efecto medido en el historial de cada producto
if any(ev.tipo == "exogena" and ev.var in X.VARIABLES_CLIMA for ev in esc.eventos):
    con_evento = dict(con_evento)
    for e in afectadas:
        f_cl = esc.factor_clima(efecto_clima, e)
        if f_cl != 1.0:
            d_e = con_evento[e].copy()
            sl = slice(esc.desde, min(esc.hasta, len(d_e)))
            for q in ("P10", "P50", "P90"):
                d_e.iloc[sl, d_e.columns.get_loc(q)] = d_e[q].iloc[sl] * f_cl
            con_evento[e] = d_e
comps = {}
for e in afectadas:
    fila = tabla_pol.loc[e]
    inv = fila["inventario"]
    par = P.Parametros(lead_time_dias=float(fila["lead_time"]), revision_dias=float(revision), nivel_servicio=nivel,
                       inventario_actual=None if pd.isna(inv) else float(inv), errores=S.errores_modelo(e))
    comps[e] = X.comparar(e, base[e], con_evento[e], par, esc, freq)

hay_inv = all(c.sim_base is not None for c in comps.values())
f_ini = base[afectadas[0]]["fecha"].iloc[min(esc.desde, H_an - 1)]
f_fin = base[afectadas[0]]["fecha"].iloc[min(esc.hasta, H_an) - 1]
descripcion = " + ".join(
    {"demanda": f"demanda {ev.valor:+.0%}", "retraso": f"retraso de {ev.valor:.0f} días",
     "precio": f"precio {ev.valor:+.0%}", "promocion": "promoción",
     "exogena": (f"{dp.etiquetas.get(ev.var, ev.var)} {ev.valor:+.0f} {'°C' if ev.var == 'temperatura' else 'mm'}"
                 if ev.var in ("temperatura", "lluvia") else f"{dp.etiquetas.get(ev.var, ev.var)} {ev.valor:+.0%}")}[ev.tipo].replace(".", ",")
    for ev in esc.eventos)
st.markdown(f"### Resultado · {descripcion}")
st.session_state["_reporte_escenario"] = dict(
    clave=S.clave_dataset(dp), descripcion=descripcion, desde=f_ini, hasta=f_fin, con_inventario=hay_inv,
    filas=[dict(entidad=str(e), **{k: comps[e].resumen.get(k, 0.0) for k in
                                   ("demanda_base_evento", "demanda_esc_evento", "perdida_extra_sin", "perdida_extra_aj")})
           for e in afectadas])
info_esc = [f"Del {f_ini:%d/%m/%Y} al {f_fin:%d/%m/%Y}", S.todos_entidad(dp) if len(afectadas) > 1 else afectadas[0],
            f"análisis sobre {H_an} {u_pl}", f"nivel de servicio {nivel}"]

if not hay_inv:
    E.nota("Para simular el inventario necesitas el inventario actual. Complétalo en el panel de "
           "<b>3. Decisiones</b> y vuelve aquí. Mientras tanto ves el efecto en la demanda y en la política.")

# ---------------------------------------------------------------- resumen de todas
if len(afectadas) > 1:
    filas = []
    for e, c in comps.items():
        r = c.resumen
        filas.append({
            nom: e,
            "Demanda en el evento": (r["demanda_esc_evento"] / r["demanda_base_evento"] - 1) * 100 if r["demanda_base_evento"] else np.nan,
            "Punto de reorden sin evento": r["rop_base"],
            "Punto de reorden con evento": r["rop_esc"],
            f"{u_pl.capitalize()} extra sin stock, sin ajustar": r.get("quiebre_extra_sin", np.nan),
            "Unidades perdidas, sin ajustar": r.get("perdida_extra_sin", np.nan),
            f"{u_pl.capitalize()} extra sin stock, ajustando": r.get("quiebre_extra_aj", np.nan),
            **({"Ingresos vs. sin evento ($)": (lambda dd: dd.iloc[1]["ingresos"] - dd.iloc[0]["ingresos"])(
                X.impacto_dinero(c, esc, S.precio(e), S.costo(e), inflacion=S.factor_inflacion(c.base["fecha"])))}
               if S.precio(e) else {}),
        })
    tabla = pd.DataFrame(filas)
    fila_res = S.fila_chips("esc_res")
    ent = S.selector_entidad(afectadas, dp, key="esc", fila=fila_res)
else:
    fila_res = S.fila_chips("esc_res")
    ent = afectadas[0]

# ---------------------------------------------------------------- detalle de una entidad
c = comps[ent]
r = c.resumen
d0, d1 = r["demanda_base_evento"], r["demanda_esc_evento"]
kp = E.Kpis()
if vista_pag != "Impacto en dinero":
    m1 = m2 = m3 = m4 = kp
    var_d = (d1 / d0 - 1) * 100 if d0 else 0
    m1.metric("Demanda durante el evento", E.num(d1), delta=f"{E.pct(var_d)} vs. sin evento" if abs(var_d) >= 0.05 else None,
              delta_color="off")
    rop_b, rop_e = r["rop_base"], r["rop_esc"]
    m2.metric("Nuevo punto de reorden", E.num(rop_e), delta=f"antes {E.num(rop_b)}", delta_color="off", delta_arrow="off",
              help="Al inicio del evento. La política se recalcula cada período con el pronóstico.")
    m3.metric("Nuevo stock de seguridad", E.num(r["ss_esc"]), delta=f"antes {E.num(r['ss_base'])}",
              delta_color="off", delta_arrow="off")
    if hay_inv:
        q = r["quiebre_extra_sin"]
        m4.metric(f"{u_pl.capitalize()} sin stock por el evento", q,
                  delta=f"{r['quiebre_extra_aj']} ajustando a tiempo", delta_color="off", delta_arrow="off",
                  help="Días (o períodos) sin stock que agrega el evento, por sobre los que ya ocurrirían sin él.")

if hay_inv and vista_pag == "Inventario":
    f_aj = c.base["fecha"].iloc[c.inicio_ajuste]
    qs, qa = r["quiebre_extra_sin"], r["quiebre_extra_aj"]
    if qs > 0:
        texto = (f"Si no haces nada, el evento deja a <b>{ent}</b> sin stock <b>{qs} {u_pl if qs != 1 else u}</b> más "
                 f"(unas <b>{E.num(r['perdida_extra_sin'])} unidades</b> sin atender). ")
        if qa == 0:
            texto += (f"Si desde el <b>{f_aj:%d/%m/%Y}</b> planificas con el escenario (punto de reorden "
                      f"<b>{E.num(rop_e)}</b> al inicio del evento), no te quedas sin stock.")
        elif qa < qs:
            texto += (f"Planificando con el escenario desde el <b>{f_aj:%d/%m/%Y}</b> los quiebres bajan a "
                      f"<b>{qa}</b> ({E.num(r['perdida_extra_aj'])} unidades). El evento empieza demasiado pronto para "
                      "evitarlos del todo: conviene un pedido urgente o un proveedor alternativo.")
        else:
            texto += "Ni ajustando la política se alcanza a evitar: conviene un pedido urgente o un proveedor alternativo."
    elif r.get("inventario_igual"):
        prox = r.get("proximo_pedido")
        texto = (f"Este evento no cambia el inventario de <b>{ent}</b>: ")
        if esc.retraso_dias() and not r.get("pedidos_en_evento"):
            texto += ("el retraso solo afecta a los pedidos que se emiten durante el evento, y entre el "
                      f"{f_ini:%d/%m/%Y} y el {f_fin:%d/%m/%Y} no se emite ninguno. ")
            texto += (f"El próximo pedido es el <b>{prox:%d/%m/%Y}</b>: mueve el evento a esa fecha para ver el efecto."
                      if prox is not None else "Con el stock actual no se emite ningún pedido en el horizonte analizado.")
        else:
            texto += "el stock actual alcanza de sobra y los pedidos no cambian."
    else:
        texto = (f"El inventario de <b>{ent}</b> aguanta este escenario sin quiebres. Durante el evento el punto de "
                 f"reorden recomendado es <b>{E.num(rop_e)}</b> (sin el evento sería {E.num(rop_b)}).")
    E.nota(texto)

x0, x1 = f_ini, f_fin + pd.tseries.frequencies.to_offset(FRECUENCIAS[freq]["pandas"])


def sombrear(fig):
    fig.add_vrect(x0=x0, x1=x1, fillcolor=E.AMARILLO, opacity=0.12, line_width=0, annotation_text="Evento",
                  annotation_position="top left", annotation_font_color=E.TINTA_2)


if vista_pag == "Demanda":
    fig = E.fig_banda(c.escenario["fecha"], c.escenario["P10"], c.escenario["P50"], c.escenario["P90"],
                      nombre_banda="Rango con el evento", nombre_p50="Con el evento", color=E.color_sku(ent))
    fig.add_trace(go.Scatter(x=c.base["fecha"], y=c.base["P50"], name="Sin el evento", mode="lines",
                             line=dict(color=E.TINTA_MUTED, width=1.6, dash="dot"), hovertemplate="%{y:,.0f}"))
    precio_ent = S.precio(ent)
    if precio_ent:
        pv = X.precio_por_periodo(precio_ent, esc, len(c.base)) * S.factor_inflacion(c.base["fecha"])
        fig.add_trace(go.Scatter(x=c.base["fecha"], y=pv, name="Precio de venta", yaxis="y2", mode="lines",
                                 line=dict(color=E.AQUA, width=1.8, shape="hv"),
                                 hovertemplate="$%{y:,.0f}"))
        rng = [min(pv) * 0.6, max(pv) * 1.08]
        fig.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False, tickprefix="$",
                                      separatethousands=True, range=rng, title=dict(text="Precio ($)"),
                                      tickfont=dict(color=E.AQUA), zeroline=False))
    sombrear(fig)
    fig.update_layout(title=f"Demanda pronosticada · {ent}", yaxis_title=dp.etiquetas["objetivo"], height=460,
                      margin=dict(t=110))
    E.grafico(fig, key="fig_esc_dem")
    kp.mostrar()
    ev_precio = [ev for ev in esc.eventos if ev.tipo == "precio"]
    otros_modelo = [ev for ev in esc.eventos if ev.tipo == "promocion"
                    or (ev.tipo == "exogena" and ev.var not in X.VARIABLES_CLIMA)]
    ev_clima = [ev for ev in esc.eventos if ev.tipo == "exogena" and ev.var in X.VARIABLES_CLIMA]
    if ev_clima and efecto_clima is not None:
        partes = []
        for ev in ev_clima:
            f = efecto_clima[(efecto_clima["entidad"] == ent) & (efecto_clima["variable"] == ev.var)
                             & efecto_clima["relevante"]]
            unidad_c = "°C" if ev.var == "temperatura" else "mm de lluvia"
            if len(f):
                partes.append(f"{E.num(float(f['efecto_pct'].iloc[0]), 1)}% por cada {unidad_c}".replace("-", "−"))
            else:
                partes.append(f"sin efecto claro de {'la temperatura' if ev.var == 'temperatura' else 'la lluvia'}")
        st.caption(f"Efecto del clima medido en el historial de {ent}: " + "; ".join(partes) + ".")
    if ev_precio and ev_precio[0].elasticidad is None and len(esc.eventos) == 1 and d0:
        el_modelo = ((d1 / d0) - 1) / ev_precio[0].valor
        st.caption(f"Efecto calculado por el modelo con lo que aprendió de tu historial: por cada 1% que sube el precio, "
                   f"la demanda cambia {E.num(el_modelo, 2)}%. Si parece muy poco, es porque tu historial tiene pocos "
                   "cambios de precio: en Opciones avanzadas puedes indicar una sensibilidad conocida.")
    elif ev_precio and ev_precio[0].elasticidad is not None:
        st.caption(f"Efecto del precio calculado con la sensibilidad que indicaste ({E.num(ev_precio[0].elasticidad, 2)}).")
    if otros_modelo:
        st.caption("El efecto de la promoción u otras variables lo calcula el modelo con lo que aprendió de tu historial.")

if vista_pag == "Inventario" and hay_inv:
    fig = go.Figure()
    if r.get("inventario_igual"):
        fig.add_trace(go.Scatter(x=c.sim_base["fecha"], y=c.sim_base["inventario"], mode="lines",
                                 name="Inventario (igual con y sin el evento)", line=dict(color=E.AZUL, width=2.2),
                                 hovertemplate="%{y:,.0f}"))
    else:
        fig.add_trace(go.Scatter(x=c.sim_base["fecha"], y=c.sim_base["inventario"], name="Sin el evento", mode="lines",
                                 line=dict(color=E.TINTA_MUTED, width=1.4, dash="dot"), hovertemplate="%{y:,.0f}"))
        fig.add_trace(go.Scatter(x=c.sim_sin_ajuste["fecha"], y=c.sim_sin_ajuste["inventario"], mode="lines",
                                 name="Con el evento, sin ajustar", line=dict(color=E.NARANJO, width=2.2),
                                 hovertemplate="%{y:,.0f}"))
        fig.add_trace(go.Scatter(x=c.sim_ajustada["fecha"], y=c.sim_ajustada["inventario"], mode="lines",
                                 name="Con el evento, ajustando", line=dict(color=E.AZUL, width=2.2),
                                 hovertemplate="%{y:,.0f}"))
    if c.sim_alternativo is not None and not r.get("inventario_igual"):
        fig.add_trace(go.Scatter(x=c.sim_alternativo["fecha"], y=c.sim_alternativo["inventario"], mode="lines",
                                 name="Proveedor alternativo", line=dict(color=E.VERDE, width=1.8, dash="dash"),
                                 hovertemplate="%{y:,.0f}"))
    qb = c.sim_sin_ajuste[c.sim_sin_ajuste["quiebre"]]
    if len(qb):
        fig.add_trace(go.Scatter(x=qb["fecha"], y=qb["inventario"], mode="markers", name="Sin stock",
                                 marker=dict(color=E.ROJO, size=8, symbol="x"),
                                 customdata=qb["no_atendida"], hovertemplate="faltan %{customdata:,.0f} u."))
    sombrear(fig)
    fig.update_layout(title=f"Inventario proyectado · {ent}", yaxis_title="Unidades", height=460,
                      margin=dict(t=110))
    E.grafico(fig, key="fig_esc_inv")
    kp.mostrar()
    st.caption("Simulación con venta perdida: cuando el inventario llega a cero, la demanda de ese día no se atiende.")
elif vista_pag == "Inventario":
    with st.container(border=True):
        st.markdown("**Política al inicio del evento**")
        t = pd.DataFrame({
            "": ["Stock de seguridad", "Punto de reorden", "Meta (T)", "Lead time (días)"],
            "Actual": [r["ss_base"], r["rop_base"], r["meta_base"], c.dec_base.L * fi["dias"]],
            "Con el evento": [r["ss_esc"], r["rop_esc"], r["meta_esc"], c.dec_esc.L * fi["dias"]],
        })
        st.dataframe(t, hide_index=True, width="stretch",
                     column_config={k: st.column_config.NumberColumn(format="%.0f") for k in ("Actual", "Con el evento")})
    kp.mostrar()

if vista_pag == "Inventario" and len(afectadas) > 1:
    st.markdown(f"#### {S.mayus(S.todos_entidad(dp))} afectad{'as' if S.es_femenino(dp) else 'os'}")
    st.dataframe(tabla.sort_values(f"{u_pl.capitalize()} extra sin stock, sin ajustar", ascending=False) if hay_inv else tabla,
                 width="stretch", hide_index=True, column_config={
                     "Demanda en el evento": st.column_config.NumberColumn(format="%+.1f%%"),
                     **{c: st.column_config.NumberColumn(format="%.0f") for c in tabla.columns[2:]},
                 })

# ---------------------------------------------------------------- impacto en dinero
# requiere precio (del archivo o asignado en Finanzas)
din = None
if vista_pag == "Impacto en dinero" and not S.precio(ent):
    E.nota(f"Para ver el impacto en dinero, asigna el precio de venta de <b>{ent}</b> en "
           "<b>4. Finanzas → Precio y costo</b>.")
if vista_pag == "Impacto en dinero" and S.precio(ent):
    precio_arch = S.precio(ent)
    costo_arch = S.costo_compra(ent)
    p_txt = st.session_state.get(f"precio_din_{ent}", float(round(precio_arch or 0)))
    c_txt = st.session_state.get(f"costo_din_{ent}", float(round(costo_arch or 0)))
    with S.chip(fila_res, f"Precio: {E.clp_corto(p_txt)}" + (f" · Costo: {E.clp_corto(c_txt)}" if c_txt else ""),
                "esc_pc", icono=":material/sell:"):
        st.caption(f"Por defecto, los valores de {ent} en Finanzas. Puedes probar otros aquí.")
        precio_ent = st.number_input("Precio de venta por unidad ($)", 0.0, None, float(round(precio_arch or 0)), 10.0,
                                     format="%.0f", key=f"precio_din_{ent}") or None
        costo_ent = st.number_input("Costo por unidad ($)", 0.0, None, float(round(costo_arch or 0)), 10.0,
                                    format="%.0f", key=f"costo_din_{ent}",
                                    help="Lo que te cuesta cada unidad que vendes. Déjalo en 0 si no lo sabes.") or None
    if costo_ent and precio_ent and costo_ent >= precio_ent:
        E.nota(f"El costo por unidad ({E.clp(costo_ent)}) es igual o mayor que el precio de venta ({E.clp(precio_ent)}). "
               "Probablemente la columna marcada como costo es el precio de un insumo (por ejemplo, por kilo) y no el costo "
               f"de una unidad de {ent}. El margen se omite: ingresa el costo real en <b>Precio</b> (arriba).")
        costo_ent = None
    din = X.impacto_dinero(c, esc, precio_ent, costo_ent, costo_arch, inflacion=S.factor_inflacion(c.base["fecha"]))
    if din is None:
        st.caption(":material/info: Ingresa el precio de venta para calcular ingresos, ventas perdidas y margen.")
    else:
        st.caption(":material/trending_up: " + S.nota_inflacion())
        d = din.set_index("mundo")
        b = d.iloc[0]
        sa = d.loc["Con el evento, sin ajustar"]
        aj = d.loc["Con el evento, ajustando"] if "Con el evento, ajustando" in d.index else None
        alt = d.loc["Con el evento, proveedor alternativo"] if "Con el evento, proveedor alternativo" in d.index else None
        hay_costo = "margen" in d.columns
        cp = esc.cambio_precio()

        k1 = k2 = k3 = k4 = kp
        k1.metric("Precio de venta en el evento", E.clp(sa["precio_evento"]),
                  delta=f"antes {E.clp_md(precio_ent)}" if abs(cp) > 1e-9 else "sin cambio",
                  delta_color="off", delta_arrow="off")
        dif_ing = sa["ingresos"] - b["ingresos"]
        k2.metric("Ingresos, sin ajustar", E.clp(sa["ingresos"]),
                  delta=f"{E.clp_md(dif_ing).replace('−', '-')} vs. sin evento" if abs(dif_ing) >= 1 else "igual que sin evento",
                  delta_color="normal" if abs(dif_ing) >= 1 else "off", delta_arrow="auto" if abs(dif_ing) >= 1 else "off")
        perd_sa = max(0.0, sa["ventas_perdidas"] - b["ventas_perdidas"])
        perd_aj = max(0.0, aj["ventas_perdidas"] - b["ventas_perdidas"]) if aj is not None else None
        k3.metric("Ventas perdidas por quiebres", E.clp(perd_sa) if hay_inv else "—",
                  delta=(f"{E.clp_md(perd_aj)} ajustando a tiempo" if perd_aj is not None else None),
                  delta_color="off", delta_arrow="off",
                  help="Unidades que no se atienden por falta de stock (por sobre las que ya faltarían sin el evento) × precio.")
        if hay_costo:
            dif_m = sa["margen"] - b["margen"]
            k4.metric("Margen, sin ajustar", E.clp(sa["margen"]),
                      delta=f"{E.clp_md(dif_m).replace('−', '-')} vs. sin evento" if abs(dif_m) >= 1 else "igual que sin evento",
                      delta_color="normal" if abs(dif_m) >= 1 else "off", delta_arrow="auto" if abs(dif_m) >= 1 else "off",
                      help=f"Ingresos menos el costo de lo vendido (costo unitario {E.clp_md(costo_ent)}).")
        else:
            k4.metric("Margen", "—", help="Ingresa el costo por unidad en «Precio y costo usados» para ver el margen.")

        frases = []
        if abs(cp) > 1e-9:
            dd = (r["demanda_esc_evento"] / r["demanda_base_evento"] - 1) * 100 if r["demanda_base_evento"] else 0
            ref_precio = aj if aj is not None else sa   # efecto del precio sin mezclarlo con los quiebres
            dif_p = ref_precio["ingresos"] - b["ingresos"]
            frases.append(f"Con el precio en <b>{E.clp(sa['precio_evento'])}</b> ({cp * 100:+.0f}%) la demanda del evento "
                          f"{'sube' if dd >= 0 else 'baja'} {E.pct(abs(dd))}; si tienes stock para atenderla, los ingresos "
                          f"del período {'suben' if dif_p >= 0 else 'bajan'} <b>{E.clp(abs(dif_p))}</b>.")
        if perd_sa > 0:
            frases.append(f"Si no reaccionas, pierdes <b>{E.clp(perd_sa)}</b> en ventas por quiebres"
                          + (f"; ajustando la política a tiempo la pérdida baja a {E.clp(perd_aj)}." if perd_aj is not None else "."))
        if alt is not None:
            col_cmp = "margen" if hay_costo else "ingresos"
            mejor_sin = max(sa[col_cmp], aj[col_cmp] if aj is not None else -np.inf)
            ganancia = alt[col_cmp] - mejor_sin - (0 if hay_costo else alt["sobrecosto"])
            if alt["sobrecosto"] <= 0:
                frases.append("El proveedor alternativo no recibe pedidos durante el evento, así que no cambia nada.")
            else:
                frases.append(f"Cubrir el retraso con el proveedor alternativo cuesta <b>{E.clp(alt['sobrecosto'])}</b> extra"
                              + (f" y deja un resultado <b>{E.clp(abs(ganancia))} {'mejor' if ganancia > 0 else 'peor'}</b> "
                                 f"que la mejor opción sin él (ajustar la política a tiempo): "
                                 f"{'conviene' if ganancia > 0 else 'no conviene'}."))
        if frases:
            E.nota(" ".join(frases))

        vista = pd.DataFrame({
            "": d.index,
            "Precio en el evento": d["precio_evento"].to_numpy(),
            "Unidades vendidas": d["unidades"].to_numpy(),
            "Ingresos": d["ingresos"].to_numpy(),
            "Ventas perdidas": d["ventas_perdidas"].to_numpy(),
        })
        if alt is not None:
            vista["Sobrecosto proveedor alternativo"] = d["sobrecosto"].to_numpy()
        if hay_costo:
            vista["Costo"] = d["costo"].to_numpy()
            vista["Margen"] = d["margen"].to_numpy()
            vista["Margen vs. sin evento"] = (d["margen"] - b["margen"]).to_numpy()

        from plotly.subplots import make_subplots
        mundos = list(d.index)
        fig_t = make_subplots(rows=1, cols=len(mundos), specs=[[{"type": "domain"}] * len(mundos)],
                              horizontal_spacing=0.04)
        partes_t = [("Vendido", "ingresos", E.AZUL), ("Perdido por quiebres", "ventas_perdidas", E.ROJO)]
        if alt is not None:
            partes_t.append(("Sobrecosto proveedor", "sobrecosto", "#9aa4b2"))
        for i, m in enumerate(mundos):
            vals = [max(0.0, float(d.loc[m, col])) if col in d.columns else 0.0 for _, col, _ in partes_t]
            tot = sum(vals) or 1.0
            centro = (f"<span style='font-size:11px;color:#6b7280'>{m.replace('Con el evento, ', 'Con evento, ')}</span>"
                      f"<br><b>{E.clp_corto(float(d.loc[m, 'ingresos']))}</b>"
                      f"<br><span style='font-size:11px;color:#6b7280'>{E.num(float(d.loc[m, 'unidades']))} unidades</span>")
            fig_t.add_trace(go.Pie(labels=[n for n, _, _ in partes_t], values=vals, hole=0.74, sort=False,
                                   marker=dict(colors=[c_ for _, _, c_ in partes_t], line=dict(color="white", width=2)),
                                   text=[E.pct(v / tot * 100, 1) if 0.005 <= v / tot < 0.995 else "" for v in vals],
                                   textinfo="text", textposition="outside",
                                   title=dict(text=centro, position="middle center", font=dict(size=13)),
                                   hovertemplate="%{label}<br>%{customdata}<extra></extra>",
                                   customdata=[E.clp(v) for v in vals], showlegend=i == 0, name=m),
                            1, i + 1)
        fig_t.update_layout(title="Ingresos y ventas perdidas", height=260, margin=dict(b=10, l=10, r=10))
        E.grafico(fig_t, key="fig_esc_dinero")
        kp.mostrar()
        st.caption(f"Sobre los {H_an} {u_pl} analizados, con el último precio"
                   + (" y costo" if hay_costo else "") + f" de tu archivo para {ent}. "
                   "Ventas perdidas = unidades sin stock × precio. "
                   + ("Margen = ingresos − unidades vendidas × costo unitario." if hay_costo else ""))

# ---------------------------------------------------------------- guardar
if registro:
    with S.chip(fila_res, "Guardar escenario", "esc_guardar", icono=":material/bookmark_add:"):
        nombre_esc = st.text_input("Nombre", value=descripcion.capitalize()[:60], key="esc_nombre")
        if st.button("Guardar", icon=":material/bookmark_add:", type="primary"):
            nuevo = dict(nombre=nombre_esc.strip() or descripcion, desde=esc.desde, duracion=esc.duracion,
                         alcance=guardado["alcance"],
                         eventos=[dict(tipo=ev.tipo, valor=ev.valor, var=ev.var, elasticidad=ev.elasticidad, costo_alt=ev.costo_alt)
                                  for ev in esc.eventos])
            lista = [x for x in registro.get("escenarios", []) if x["nombre"] != nuevo["nombre"]] + [nuevo]
            registro["escenarios"] = lista
            S.actualizar_registro(escenarios=lista)
            st.toast(f"Escenario “{nuevo['nombre']}” guardado en Mis pronósticos.", icon=":material/bookmark_added:")
elif cuenta.login_disponible() and not cuenta.usuario():
    st.caption(":material/lock_open: Inicia sesión para guardar tus escenarios y volver a ellos.")

S.info_pie(info_esc)

# ---------------------------------------------------------------- descarga
hojas = {}
if len(afectadas) > 1:
    hojas["Resumen"] = tabla.round(1)
det = c.base[["fecha", "P50"]].rename(columns={"P50": "Demanda sin evento"})
det["Demanda con evento"] = c.escenario["P50"].to_numpy()
if hay_inv:
    det["Inventario sin evento"] = c.sim_base["inventario"].to_numpy()
    det["Inventario con evento, sin ajustar"] = c.sim_sin_ajuste["inventario"].to_numpy()
    det["No atendida, sin ajustar"] = c.sim_sin_ajuste["no_atendida"].to_numpy()
    det["Inventario con evento, ajustando"] = c.sim_ajustada["inventario"].to_numpy()
    det["Pedido, ajustando"] = c.sim_ajustada["pedido"].to_numpy()
if S.precio(ent):
    det["Precio de venta"] = X.precio_por_periodo(S.precio(ent), esc, len(det)) * S.factor_inflacion(det["fecha"])
det["fecha"] = det["fecha"].dt.date
if din is not None:
    hojas["Impacto en dinero"] = vista.rename(columns={"": "Mundo"}).round(0)
hojas[f"Detalle {ent}"[:31]] = det.rename(columns={"fecha": "Fecha"}).round(1)
with st.container(horizontal=True, horizontal_alignment="right"):
    st.download_button("Descargar escenario (Excel)", E.excel_bytes(hojas), file_name="escenario.xlsx",
                       icon=":material/download:", type="tertiary",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

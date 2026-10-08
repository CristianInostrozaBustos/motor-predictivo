"""Textos con IA (Claude por API).

La IA no calcula: recibe un resumen de los números que ya calcularon los motores y los explica o señala lo que
llama la atención. Sin clave configurada, el sitio sigue con sus textos automáticos.
"""

from __future__ import annotations

import json
import re

# Por rol, en orden de preferencia; si un modelo no está disponible en la cuenta se prueba el siguiente.
MODELOS = {
    "rapido": ("claude-haiku-5-5", "claude-haiku-4-5"),
    "asistente": ("claude-sonnet-5-5", "claude-sonnet-5"),
}

SISTEMA = (
    "Eres el analista de un sitio web de pronóstico de demanda e inventario usado por pymes y estudiantes. "
    "Escribes en español de Chile, en tono profesional y cercano, con frases cortas y concretas. "
    "Reglas: usa solo las cifras entregadas en los datos, nunca inventes ni calcules cifras nuevas salvo diferencias "
    "o porcentajes simples entre cifras entregadas; no nombres algoritmos ni modelos; no uses paréntesis ni listas "
    "salvo que se pidan; no repitas el nombre del sitio; si un dato falta, no lo menciones."
)


class ErrorIA(Exception):
    pass


class ClienteIA:
    def __init__(self, api_key: str, modelos: dict | None = None, cliente=None):
        if cliente is None:
            import anthropic
            cliente = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=60)
        self.cliente = cliente
        self.modelos = {r: tuple(v) for r, v in MODELOS.items()}
        for r, m in (modelos or {}).items():
            if m:
                self.modelos[r] = (m,) + tuple(x for x in self.modelos.get(r, ()) if x != m)
        self.usado = {}

    def texto(self, rol: str, mensaje: str, max_tokens: int = 1200, sistema: str = SISTEMA) -> str:
        ultimo = None
        for m in self.modelos[rol]:
            try:
                r = self.cliente.messages.create(model=m, max_tokens=max_tokens, system=sistema,
                                                 messages=[{"role": "user", "content": mensaje}])
            except Exception as e:  # noqa: BLE001
                if type(e).__name__ == "NotFoundError":      # modelo no disponible en la cuenta
                    ultimo = e
                    continue
                raise ErrorIA(_mensaje_error(e)) from e
            self.usado[rol] = m
            return "".join(getattr(b, "text", "") for b in r.content).strip()
        raise ErrorIA("Ningún modelo configurado está disponible en la cuenta.") from ultimo

    def json(self, rol: str, instruccion: str, datos: dict, max_tokens: int = 1500) -> dict:
        mensaje = (f"{instruccion}\n\nResponde solo con un objeto JSON válido, sin texto antes ni después.\n\n"
                   f"<datos>\n{json.dumps(datos, ensure_ascii=False, default=str)}\n</datos>")
        crudo = self.texto(rol, mensaje, max_tokens)
        return extraer_json(crudo)


def extraer_json(texto: str) -> dict:
    t = re.sub(r"^```(?:json)?|```$", "", texto.strip(), flags=re.M).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ErrorIA("La respuesta no trae JSON.")
    try:
        return json.loads(t[i:j + 1])
    except json.JSONDecodeError as e:
        raise ErrorIA("La respuesta trae un JSON inválido.") from e


def _mensaje_error(e) -> str:
    n = type(e).__name__
    return {
        "AuthenticationError": "La clave de IA no es válida.",
        "PermissionDeniedError": "La clave de IA no tiene permiso para este uso.",
        "RateLimitError": "Se alcanzó el límite de uso de la IA; intenta en un momento.",
        "APITimeoutError": "La IA tardó demasiado en responder.",
        "APIConnectionError": "No se pudo conectar con el servicio de IA.",
    }.get(n, "El servicio de IA no respondió" + (": saldo insuficiente." if "credit" in str(e).lower() else "."))


# ---------------------------------------------------------------- tareas
def redactar_reporte(cli: ClienteIA, datos: dict, borradores: dict) -> dict:
    """Párrafos del reporte PDF. borradores: {clave: texto automático}. Devuelve {clave: texto} con las mismas
    claves; las que falten se quedan con el texto automático."""
    instruccion = (
        "Redacta los párrafos de un reporte PDF de pronóstico. Para cada clave de 'borradores' escribe un párrafo "
        "de 2 a 4 frases que explique qué muestran los números de esa sección y qué significan para quien decide "
        "compras e inventario. Usa los borradores como base de cifras, mejora la redacción y agrega una "
        "interpretación útil. No uses negritas ni markdown. Devuelve {\"clave\": \"párrafo\", ...} con exactamente "
        "las mismas claves.")
    r = cli.json("rapido", instruccion, {"datos": datos, "borradores": borradores}, max_tokens=4000)
    return {k: str(v).strip() for k, v in r.items() if k in borradores and str(v).strip()}


def analizar(cli: ClienteIA, datos: dict) -> dict:
    """Lectura del pronóstico: resumen breve y alertas sobre lo que conviene revisar."""
    instruccion = (
        "Revisa este pronóstico como lo haría un analista de demanda. Devuelve "
        "{\"resumen\": \"3 a 5 frases con lo principal\", \"alertas\": [{\"nivel\": \"alta\" o \"media\", "
        "\"texto\": \"una frase con el problema y qué revisar\"}]}. Busca cosas como: errores altos frente al resto, "
        "un modelo que no supera a repetir la temporada anterior, rangos muy anchos, cambios bruscos frente al "
        "historial reciente, productos en riesgo de quiebre o sin inventario, poca historia. Máximo 5 alertas, "
        "ordenadas por importancia; lista vacía si no hay nada relevante.")
    r = cli.json("rapido", instruccion, datos, max_tokens=1500)
    alertas = [a for a in (r.get("alertas") or []) if isinstance(a, dict) and a.get("texto")]
    return {"resumen": str(r.get("resumen", "")).strip(),
            "alertas": [{"nivel": "alta" if a.get("nivel") == "alta" else "media", "texto": str(a["texto"]).strip()}
                        for a in alertas[:5]]}

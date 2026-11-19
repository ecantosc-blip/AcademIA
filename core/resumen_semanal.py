"""
Resumen semanal con IA (Fase 5).

Distinto de resumen_semana() en core/query_engine.py (esa es una concatenacion de
texto fija para el chat, sin IA). Aqui el modelo REDACTA un resumen breve, pero
solo puede usar los hechos que se le entregan: se le fuerza a llamar a una
herramienta con un esquema fijo (igual que crear_practica/sugerir_busquedas en
core/estudio.py), nunca texto libre que se pueda desviar hacia datos inventados.

Que cuenta como "completado esta semana": tareas que Moodle confirmo (columna
verificada_en de la tabla entrega) en los ultimos 7 dias. Es la fecha en que SE
SUPO que estaba entregada, no necesariamente el dia en que el estudiante la hizo
(Moodle no entrega ese dato); se explica asi en la propia tarjeta de la web.

Si la IA no esta disponible (sin API key, sin red, error del modelo), nunca se
rompe el resumen: se devuelven los mismos hechos sin redactar y "generado": False.
"""
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import notas_moodle
from core.tareas import clasificar_tareas, en_ventana
from core.tiempo import ahora_iso

MODELO = os.getenv("RESUMEN_MODEL", "claude-sonnet-4-6")
MAX_SUGERENCIAS = 3

HERRAMIENTA = {
    "name": "generar_resumen",
    "description": "Redacta el resumen semanal del estudiante a partir, unicamente, de los hechos que se le entregan.",
    "input_schema": {
        "type": "object",
        "properties": {
            "completado": {"type": "array", "items": {"type": "string"},
                           "description": "Lo que se confirmo hecho esta semana. Lista vacia si no hay nada."},
            "pendiente": {"type": "array", "items": {"type": "string"},
                          "description": "Tareas pendientes confirmadas o vencidas sin entregar."},
            "proximas_evaluaciones": {"type": "array", "items": {"type": "string"}},
            "sugerencias": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_SUGERENCIAS,
                            "description": "Como maximo 3, basadas solo en los hechos entregados."},
            "mensaje": {"type": "string", "description": "Parrafo corto (2-3 frases), cercano, sin tecnicismos."},
        },
        "required": ["completado", "pendiente", "proximas_evaluaciones", "sugerencias", "mensaje"],
    },
}

SYSTEM = """Eres quien redacta el resumen semanal de AcademIA para un estudiante de la UNEMI.

Reglas estrictas, sin excepcion:
- Solo puedes mencionar los hechos que se te dan en el mensaje. No inventes tareas, materias, fechas,
  notas ni porcentajes que no esten ahi.
- No juzgas notas ni asistencia: describes el dato, nunca concluyes si esta "bien" o "mal". (La
  asistencia SI tiene un minimo oficial del reglamento de la UNEMI -ver core/seguimiento.py-, pero
  ese veredicto lo da unicamente esa regla fija, no este resumen redactado por IA.)
- Las tareas de la lista "sin_verificar" NO estan confirmadas como entregadas ni como pendientes (Moodle
  no informo su estado): si las mencionas, dilo asi explicitamente, nunca como si ya se supiera su estado.
- Las sugerencias (maximo 3) deben salir de los propios hechos entregados (ej. una tarea vencida sin
  entregar, o una materia con menor porcentaje en Moodle que las demas); concretas y accionables.
- Tono cercano, en espanol de Ecuador, sin tecnicismos y sin emojis.
- Si una categoria no tiene nada, dilo con naturalidad en vez de dejarla vacia sin explicacion
  (ej. "Esta semana no tienes evaluaciones proximas registradas.").

Llama siempre a la herramienta "generar_resumen" con el resultado; no respondas en texto libre."""


def _hechos(conn, estudiante_id: int, ahora: str) -> dict:
    """Solo datos ya verificados, reutilizando el criterio unico de core/tareas.py y
    el mismo orden por porcentaje de Moodle que ya usa core/planificador.py."""
    grupos = clasificar_tareas(conn, estudiante_id, ahora=ahora)
    desde_7d = ahora_iso("seconds", days=-7)
    hasta_7d = ahora_iso("seconds", days=7)

    completadas = [t for t in grupos["entregadas"] if t["verificada_en"] and t["verificada_en"] >= desde_7d]
    proximas = en_ventana(grupos["pendientes"], ahora, hasta_7d)
    sin_verificar = [t for t in grupos["sin_verificar"] if not t["vencida"]]

    eventos = conn.execute("""
        SELECT nombre, fecha FROM evento WHERE estudiante_id = ? AND fecha >= ? AND fecha <= ? ORDER BY fecha
    """, (estudiante_id, ahora, hasta_7d)).fetchall()

    promedios = notas_moodle.promedios(conn, estudiante_id)
    prioridad = notas_moodle.prioridad(conn, estudiante_id)
    nombres_materia = {r["id"]: r["nombre"] for r in conn.execute("SELECT id, nombre FROM materia")}
    debiles = sorted(prioridad, key=lambda m: prioridad[m])[:2]

    return {
        "completado_confirmado_esta_semana": [f"{t['tarea']} ({t['materia']})" for t in completadas],
        "pendiente_confirmado": [f"{t['tarea']} ({t['materia']}) - vence {t['fecha'][:10]}" if t["fecha"]
                                  else f"{t['tarea']} ({t['materia']}) - sin fecha" for t in proximas],
        "vencidas_sin_entregar": [f"{t['tarea']} ({t['materia']}) - vencio {t['fecha'][:10]}" for t in grupos["vencidas_sin_entregar"]],
        "sin_verificar_moodle": [f"{t['tarea']} ({t['materia']})" for t in sin_verificar],
        "evaluaciones_proximos_7_dias": [f"{e['nombre']} - {e['fecha'][:10]}" for e in eventos],
        "materias_con_menor_porcentaje_moodle": [f"{nombres_materia.get(m, 'materia')}: {promedios[m]:.0f}%" for m in debiles],
    }


def generar(conn, estudiante_id: int, client=None, model: str = MODELO, ahora: str = None) -> dict:
    """Devuelve {completado, pendiente, proximas_evaluaciones, sugerencias, mensaje, generado, hechos}.
    `generado` es False cuando la IA no respondio y se muestran los hechos sin redactar (nunca se rompe
    el panel por falta de API key o de red)."""
    ahora = ahora or ahora_iso("seconds")
    hechos = _hechos(conn, estudiante_id, ahora)
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    try:
        resp = client.messages.create(
            model=model, max_tokens=1024, system=SYSTEM,
            tools=[HERRAMIENTA], tool_choice={"type": "tool", "name": "generar_resumen"},
            messages=[{"role": "user", "content": "Hechos de esta semana (no agregues nada que no este aqui):\n"
                                                    + json.dumps(hechos, ensure_ascii=False)}],
        )
        bloque = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
        if bloque is None:
            raise ValueError("El modelo no devolvio el resumen estructurado.")
        resultado = dict(bloque.input)
        for clave in ("completado", "pendiente", "proximas_evaluaciones", "sugerencias"):
            resultado[clave] = list(resultado.get(clave) or [])[:MAX_SUGERENCIAS if clave == "sugerencias" else None]
        resultado["mensaje"] = str(resultado.get("mensaje") or "").strip()
        resultado["generado"] = True
    except Exception:
        resultado = {
            "completado": hechos["completado_confirmado_esta_semana"],
            "pendiente": hechos["pendiente_confirmado"] + hechos["vencidas_sin_entregar"],
            "proximas_evaluaciones": hechos["evaluaciones_proximos_7_dias"],
            "sugerencias": [],
            "mensaje": "No se pudo redactar el resumen con IA en este momento; se muestran los datos tal cual, sin redactar.",
            "generado": False,
        }
    resultado["hechos"] = hechos
    return resultado

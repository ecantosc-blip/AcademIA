"""
Herramientas del chatbot para armar el plan de estudio conversando.

El asistente (Claude) hace las preguntas y estas funciones guardan lo que la persona confirma y
generan el plan con el planificador explicable (core/planificador.py). El modelo NO calcula horarios:
solo conversa; las horas salen del codigo. Cada funcion devuelve texto para que el modelo lo redacte,
y los errores de validacion vuelven como texto para que pueda corregir y reintentar.
"""
from core import agenda_personal as AP
from core import planificador
from core.preferencias import DIAS

HERRAMIENTAS = [
    {
        "name": "ver_horario_personal",
        "description": "Muestra las franjas en que el estudiante dijo que puede estudiar y las actividades de su dia a dia que tiene guardadas, mas las clases de su horario del ERP. Usala ANTES de preguntar, para no repetir lo que ya se sabe.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "guardar_franjas_libres",
        "description": "Guarda (REEMPLAZANDO todo lo anterior) las franjas en que el estudiante PUEDE estudiar. Llamala solo despues de que el estudiante confirmo el resumen. Una lista vacia borra las franjas.",
        "input_schema": {
            "type": "object",
            "properties": {"franjas": {"type": "array", "items": {"type": "object", "properties": {
                "dia": {"type": "string", "description": "lunes, martes, miercoles, jueves, viernes, sabado o domingo"},
                "inicio": {"type": "string", "description": "HH:MM en 24 horas, ej. 18:30"},
                "fin": {"type": "string", "description": "HH:MM en 24 horas"}},
                "required": ["dia", "inicio", "fin"]}}},
            "required": ["franjas"],
        },
    },
    {
        "name": "guardar_actividades",
        "description": "Guarda (REEMPLAZANDO todo lo anterior) las actividades del dia a dia del estudiante (trabajo, transporte, deporte, familia...) que ocupan su tiempo. Llamala solo despues de que el estudiante confirmo. Una lista vacia las borra.",
        "input_schema": {
            "type": "object",
            "properties": {"actividades": {"type": "array", "items": {"type": "object", "properties": {
                "dia": {"type": "string"}, "inicio": {"type": "string", "description": "HH:MM"},
                "fin": {"type": "string", "description": "HH:MM"},
                "nombre": {"type": "string", "description": "Que hace, en pocas palabras"}},
                "required": ["dia", "inicio", "fin", "nombre"]}}},
            "required": ["actividades"],
        },
    },
    {
        "name": "generar_plan_estudio",
        "description": "Genera el plan de estudio de los proximos 7 dias con las horas realmente libres (franjas menos clases y actividades), dando prioridad a las materias con menor porcentaje en Moodle. Devuelve el plan ya calculado: presentalo sin cambiar horas ni minutos.",
        "input_schema": {"type": "object", "properties": {}},
    },
]
NOMBRES = {h["name"] for h in HERRAMIENTAS}


def _rango(f):
    return f"{f['inicio']}-{f['fin']}"


def ver_horario_personal(conn, est) -> str:
    d = AP.listar(conn, est)
    lineas = []
    for dia in DIAS:
        fr = [_rango(f) for f in d["franjas"] if f["dia"] == dia]
        ac = [f"{_rango(a)} {a['nombre']}" for a in d["actividades"] if a["dia"] == dia]
        if fr or ac:
            lineas.append(f"- {dia}: libre {', '.join(fr) or 'sin franjas'}" + (f"; ocupado: {', '.join(ac)}" if ac else ""))
    clases = AP._clases(conn, est)
    cl = [f"- {dia}: " + ", ".join(f"{AP.a_texto(i)}-{AP.a_texto(f)}" for i, f in sorted(clases[dia])) for dia in DIAS if dia in clases]
    texto = "Franjas y actividades guardadas:\n" + ("\n".join(lineas) if lineas else "- (nada guardado todavia)")
    texto += "\n\nClases del ERP (ya se descuentan solas, no hace falta preguntarlas):\n" + ("\n".join(cl) if cl else "- (no hay horario sincronizado)")
    return texto


def guardar_franjas_libres(conn, est, franjas=None) -> str:
    try:
        n = AP.guardar_franjas(conn, est, franjas or [])
    except ValueError as e:
        return f"No se guardó nada: {e} Pídele al estudiante que lo aclare."
    return f"Listo: {n} franja(s) libre(s) guardada(s)." if n else "Se borraron las franjas libres."


def guardar_actividades(conn, est, actividades=None) -> str:
    try:
        n = AP.guardar_actividades(conn, est, actividades or [])
    except ValueError as e:
        return f"No se guardó nada: {e} Pídele al estudiante que lo aclare."
    return f"Listo: {n} actividad(es) guardada(s)." if n else "Se borraron las actividades."


def generar_plan_estudio(conn, est) -> str:
    p = planificador.generar(conn, est)
    if not p["configurado"]:
        return ("Todavía no hay horas libres para planificar: el estudiante no ha indicado franjas en las que pueda "
                "estudiar (o sus clases y actividades las ocupan todas). Pregúntale su disponibilidad.")
    L = ["PLAN DE ESTUDIO (calculado por la app; no cambies horas ni minutos):"]
    for d in p["dias"]:
        if not d["bloques"]:
            continue
        L.append(f"\n{d['dia'].capitalize()} {d['fecha']}:")
        for b in d["bloques"]:
            hora = f"{b['inicio']}-{b['fin']} " if b["inicio"] else ""
            L.append(f"- {hora}({b['minutos']} min) {b['item']}" + (f" [{b['materia']}]" if b["materia"] and b["tipo"] != "refuerzo" else ""))
            L.append(f"    por qué: {b['motivo']}")
            L.append(f"    cómo estudiar: {b['recomendacion']}")
    if p["no_alcanza"]:
        L.append("\nNO ALCANZA EL TIEMPO para:")
        L += [f"- {x['item']}: faltan {x['faltan_min']} min antes de la fecha límite" for x in p["no_alcanza"]]
    if p["prioridad_moodle"]:
        L.append("\nMaterias con menor porcentaje en Moodle (las que se priorizan): " +
                 ", ".join(f"{x['materia']} ({x['porcentaje']} %)" for x in p["prioridad_moodle"][:2]))
    else:
        L.append("\nNo se pudo priorizar por notas: Moodle no informó porcentajes comparables de tus materias.")
    if p["sin_verificar"]:
        L.append(f"\nHay {len(p['sin_verificar'])} tarea(s) SIN VERIFICAR que no entran en el plan: que las revise en Moodle.")
    L.append("\nSupuestos: " + " | ".join(p["supuestos"]))
    return "\n".join(L)


FUNCIONES = {"ver_horario_personal": ver_horario_personal, "guardar_franjas_libres": guardar_franjas_libres,
             "guardar_actividades": guardar_actividades, "generar_plan_estudio": generar_plan_estudio}


def ejecutar(nombre: str, conn, est, args: dict) -> str:
    return FUNCIONES[nombre](conn, est, **(args or {}))

"""
Herramientas del chatbot para armar el plan de estudio conversando.

El asistente (Claude) hace las preguntas y estas funciones guardan lo que la persona confirma y
generan el plan con el planificador explicable (core/planificador.py). El modelo NO calcula horarios:
solo conversa; las horas salen del codigo. Cada funcion devuelve texto para que el modelo lo redacte,
y los errores de validacion vuelven como texto para que pueda corregir y reintentar.
"""
import unicodedata

from core import agenda_personal as AP
from core import bloques_fijos
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
        "name": "hacer_pregunta",
        "description": "Muestra una pregunta CERRADA con botones para que el estudiante toque en vez de escribir (tipo encuesta). Usala para preguntas con pocas respuestas posibles: momento del dia preferido, que actividades hace, dias, dificultad, si confirma un resumen. Con varias=true puede marcar varias opciones. Despues de llamarla, escribe SOLO la pregunta en tu mensaje y espera la respuesta; no llames otras herramientas en ese turno. El estudiante siempre puede escribir otra cosa.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pregunta": {"type": "string", "description": "La pregunta, corta"},
                "opciones": {"type": "array", "items": {"type": "string"}, "description": "De 2 a 8 opciones cortas"},
                "varias": {"type": "boolean", "description": "true si puede elegir varias"}},
            "required": ["pregunta", "opciones"],
        },
    },
    {
        "name": "agregar_actividad",
        "description": "AGREGA una actividad de rutina (desayuno, almuerzo, cena, gimnasio, trabajo...) en uno o varios dias SIN borrar las demas. Preferila a guardar_actividades cuando solo hay que sumar algo. Confirma antes con el estudiante.",
        "input_schema": {"type": "object", "properties": {
            "nombre": {"type": "string"}, "dias": {"type": "array", "items": {"type": "string"}, "description": "lunes..domingo"},
            "inicio": {"type": "string", "description": "HH:MM"}, "fin": {"type": "string", "description": "HH:MM"}},
            "required": ["nombre", "dias", "inicio", "fin"]},
    },
    {
        "name": "quitar_actividad",
        "description": "Quita actividades de rutina por nombre (y opcionalmente un dia).",
        "input_schema": {"type": "object", "properties": {"nombre": {"type": "string"}, "dia": {"type": "string"}}, "required": ["nombre"]},
    },
    {
        "name": "mover_bloque_estudio",
        "description": "Mueve un bloque de estudio del plan a otro dia u hora y lo FIJA ahi (el plan lo respeta). Identificalo por una parte de su nombre. No puede chocar con clases ni actividades. Confirma con el estudiante si hay duda.",
        "input_schema": {"type": "object", "properties": {
            "texto": {"type": "string", "description": "Parte del nombre del bloque (tarea, evaluacion o 'refuerzo')"},
            "dia": {"type": "string", "description": "Dia destino: lunes..domingo o fecha AAAA-MM-DD"},
            "inicio": {"type": "string", "description": "Hora de inicio HH:MM"},
            "desde_dia": {"type": "string", "description": "Solo si el bloque aparece en varios dias: de que dia moverlo"}},
            "required": ["texto", "dia", "inicio"]},
    },
    {
        "name": "soltar_bloques_fijados",
        "description": "Quita todos los bloques fijados por el estudiante para que el plan los vuelva a acomodar solo.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "generar_plan_estudio",
        "description": "Genera el plan de estudio de los proximos 7 dias con las horas realmente libres (franjas menos clases y actividades), dando prioridad a las materias con menor porcentaje en Moodle. Devuelve el plan ya calculado: presentalo sin cambiar horas ni minutos.",
        "input_schema": {"type": "object", "properties": {}},
    },
]
NOMBRES = {h["name"] for h in HERRAMIENTAS} - {"hacer_pregunta"}      # hacer_pregunta la maneja el asistente (botones)


def _rango(f):
    return f"{f['inicio']}-{f['fin']}"


def ver_horario_personal(conn, est) -> str:
    """Herramienta del chatbot: resume por dia las franjas libres y las actividades del estudiante."""
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
    """Herramienta del chatbot: calcula el plan con el planificador y lo devuelve como texto. Las horas las pone el codigo; la IA no debe cambiarlas."""
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


def _sin_tildes(t):
    return unicodedata.normalize("NFD", str(t or "")).encode("ascii", "ignore").decode().lower().strip()


def agregar_actividad(conn, est, nombre=None, dias=None, inicio=None, fin=None) -> str:
    try:
        if not isinstance(dias, list) or not dias:
            raise ValueError("Indica en qué días.")
        AP.agregar(conn, est, "actividad", [{"dia": d, "inicio": inicio, "fin": fin, "nombre": nombre} for d in dias])
    except ValueError as e:
        return f"No se guardó nada: {e} Pídele al estudiante que lo aclare."
    return f"Listo: «{nombre}» agregada ({inicio}-{fin}) en {len(dias)} día(s)."


def quitar_actividad(conn, est, nombre=None, dia=None) -> str:
    """Herramienta del chatbot: borra una actividad de la rutina por nombre (y dia opcional). Si hay ambiguedad, pide precisar."""
    quiero = _sin_tildes(nombre)
    dia_n = None
    if dia:
        try:
            dia_n = AP.normalizar_dia(dia)
        except ValueError as e:
            return f"No se quitó nada: {e}"
    ids = [a["id"] for a in AP.listar(conn, est)["actividades"]
           if quiero and quiero in _sin_tildes(a["nombre"]) and (dia_n is None or a["dia"] == dia_n)]
    if not ids:
        return "No encontré actividades con ese nombre; no se quitó nada."
    for i in ids:
        AP.borrar(conn, est, "actividad", i)       # los ids cambian al guardar: se vuelve a buscar cada vez
        resto = [a["id"] for a in AP.listar(conn, est)["actividades"]
                 if quiero in _sin_tildes(a["nombre"]) and (dia_n is None or a["dia"] == dia_n)]
        if not resto:
            break
        ids = resto
    return "Listo: actividades quitadas."


def _fecha_destino(plan, dia):
    """'martes' -> primera fecha del plan que cae en martes; 'AAAA-MM-DD' se acepta tal cual si esta en el plan."""
    fechas = [d["fecha"] for d in plan["dias"]]
    if str(dia).strip() in fechas:
        return str(dia).strip()
    try:
        nombre = AP.normalizar_dia(dia)
    except ValueError:
        raise ValueError(f"«{dia}» no es un día válido (lunes a domingo o una fecha del plan).") from None
    return next((d["fecha"] for d in plan["dias"] if d["dia"] == nombre), None)


def mover_bloque_estudio(conn, est, texto=None, dia=None, inicio=None, desde_dia=None) -> str:
    """Herramienta del chatbot: mueve un bloque de estudio a otro dia/hora y lo deja fijado. Valida choques con clases, actividades y otros fijados."""
    plan = planificador.generar(conn, est)
    quiero = _sin_tildes(texto)
    if not quiero:
        return "Indica qué bloque mover."
    cand = [(d, b) for d in plan["dias"] for b in d["bloques"] if b.get("inicio") and quiero in _sin_tildes(b["item"])]
    if desde_dia:
        try:
            n = AP.normalizar_dia(desde_dia)
            cand = [(d, b) for d, b in cand if d["dia"] == n]
        except ValueError:
            pass
    if not cand:
        return "No encontré ese bloque en el plan."
    if len({b["clave"] for _, b in cand}) > 1:
        return "Hay varios bloques que coinciden: " + "; ".join(sorted({b["item"] for _, b in cand})) + ". Pregúntale cuál."
    if len(cand) > 1:
        return ("Ese bloque aparece en varios días (" + ", ".join(f"{d['dia']} {b['inicio']}" for d, b in cand) +
                "). Pregúntale de qué día moverlo y usa desde_dia.")
    d, b = cand[0]
    try:
        fecha = _fecha_destino(plan, dia)
        if not fecha:
            return "Ese día no está dentro de la semana del plan."
        r = bloques_fijos.mover(conn, est, b["clave"], d["fecha"], b["inicio"], fecha, inicio)
    except ValueError as e:
        return f"No se movió: {e}"
    return f"Listo: «{b['item']}» quedó fijado el {r['fecha']} de {r['inicio']} a {r['fin']}."


def soltar_bloques_fijados(conn, est) -> str:
    n = bloques_fijos.soltar(conn, est)
    return f"Listo: {n} bloque(s) volvieron a acomodarse solos." if n else "No había bloques fijados."


FUNCIONES = {"ver_horario_personal": ver_horario_personal, "guardar_franjas_libres": guardar_franjas_libres,
             "guardar_actividades": guardar_actividades, "generar_plan_estudio": generar_plan_estudio,
             "agregar_actividad": agregar_actividad, "quitar_actividad": quitar_actividad,
             "mover_bloque_estudio": mover_bloque_estudio, "soltar_bloques_fijados": soltar_bloques_fijados}


def ejecutar(nombre: str, conn, est, args: dict) -> str:
    return FUNCIONES[nombre](conn, est, **(args or {}))

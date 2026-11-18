"""
Asistente inteligente - version 2 (con LLM real, patron RAG/tool-calling).

En vez del router de reglas simples de query_engine.py (deteccion de
palabras clave), aqui un modelo de lenguaje (Claude) decide que funcion
llamar segun la pregunta, y luego redacta la respuesta final en base a
los datos reales que devuelve esa funcion. Esto es lo que evita que el
modelo "invente" notas o fechas: solo puede hablar de lo que la funcion
realmente devolvio.

Requiere una API key de Anthropic (console.anthropic.com) en el .env:
    ANTHROPIC_API_KEY=sk-ant-...
"""
import os
import sys
import threading

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.query_engine import QueryEngine
from core import plan_chat

import anthropic


# Definicion de las "herramientas" que el modelo puede invocar. Cada una
# corresponde 1 a 1 con un metodo de QueryEngine.
TOOLS = [
    {
        "name": "tareas_pendientes",
        "description": "Obtiene las tareas del estudiante de todas las materias, separadas en pendientes confirmadas, vencidas sin entregar y SIN VERIFICAR (Moodle no informo si ya las entrego).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "tareas_por_materia",
        "description": "Obtiene las tareas de una materia especifica: pendientes confirmadas, vencidas sin entregar y SIN VERIFICAR (Moodle no informo si ya las entrego).",
        "input_schema": {
            "type": "object",
            "properties": {"nombre_materia": {"type": "string", "description": "Nombre de la materia"}},
            "required": ["nombre_materia"],
        },
    },
    {
        "name": "calificacion_materia",
        "description": "Obtiene la nota final y asistencia de una materia especifica.",
        "input_schema": {
            "type": "object",
            "properties": {"nombre_materia": {"type": "string"}},
            "required": ["nombre_materia"],
        },
    },
    {
        "name": "calificaciones_detalladas",
        "description": "Obtiene el detalle de calificaciones por componente (parciales, tareas, etc.) de una materia.",
        "input_schema": {
            "type": "object",
            "properties": {"nombre_materia": {"type": "string"}},
            "required": ["nombre_materia"],
        },
    },
    {
        "name": "comparar_materias",
        "description": "Compara las notas entre todas las materias para saber en cual va mejor o peor.",
        "input_schema": {
            "type": "object",
            "properties": {"peor": {"type": "boolean", "description": "true para la nota mas baja, false para la mas alta"}},
        },
    },
    {
        "name": "nota_necesaria",
        "description": "Calcula cuantos puntos le faltan al estudiante para aprobar una materia.",
        "input_schema": {
            "type": "object",
            "properties": {"nombre_materia": {"type": "string"}},
            "required": ["nombre_materia"],
        },
    },
    {
        "name": "proximos_eventos",
        "description": "Obtiene los proximos eventos y examenes del calendario academico.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "horario_materia",
        "description": "Obtiene el horario de clases, opcionalmente filtrado por materia.",
        "input_schema": {
            "type": "object",
            "properties": {"nombre_materia": {"type": "string"}},
        },
    },
    {
        "name": "profesor_materia",
        "description": "Obtiene el nombre y correo del profesor de una materia especifica.",
        "input_schema": {
            "type": "object",
            "properties": {"nombre_materia": {"type": "string"}},
            "required": ["nombre_materia"],
        },
    },
    {
        "name": "rendimiento_materia",
        "description": "Muestra el desglose de calificaciones por componente (N1, N2, N3, N4, EXP1, EXP2, examen) de una materia, con el puntaje ponderado obtenido hasta ahora sobre 100.",
        "input_schema": {
            "type": "object",
            "properties": {"nombre_materia": {"type": "string"}},
            "required": ["nombre_materia"],
        },
    },
    {
        "name": "materias_por_semestre",
        "description": "Lista todas las materias de la malla curricular que corresponden a un semestre/nivel especifico (ej. 'materias del 5to semestre').",
        "input_schema": {
            "type": "object",
            "properties": {"semestre": {"type": "integer", "description": "Numero de semestre, ej. 5"}},
            "required": ["semestre"],
        },
    },
    {
        "name": "notas_por_semestre",
        "description": "Muestra las notas (y estado aprobada/reprobada) que el estudiante sacó en las materias de un semestre/nivel de la malla, ej. 'con qué notas pasé el primer semestre' -> semestre=1. Úsala para preguntas sobre las notas de un semestre concreto.",
        "input_schema": {
            "type": "object",
            "properties": {"semestre": {"type": "integer", "description": "Numero de semestre, ej. 1"}},
            "required": ["semestre"],
        },
    },
    {
        "name": "materias_faltantes",
        "description": "Lista las materias que el estudiante aun no ha aprobado segun su malla curricular (lo que le falta para graduarse).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "materias_aprobadas",
        "description": "Lista todas las materias que el estudiante ha aprobado en cualquier semestre (historial completo, no solo el actual).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "record_academico",
        "description": "Muestra el registro academico completo del estudiante: todas las materias cursadas en todos los semestres, con su estado (aprobada/reprobada/pendiente) y nota.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "materias_actuales",
        "description": "Lista las materias que el estudiante cursa AHORA MISMO (estado en_curso), con su nota si esta disponible. Usala para '¿qué materias curso?'/'¿qué materias llevo?' sin preguntar el semestre: ya se sabe por la matrícula sincronizada.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "rendimiento_actual",
        "description": "Nota ponderada (N1-N4, EXP1-EXP2, examen) de CADA materia que el estudiante cursa ahora, calculada con los componentes que ya sincronizó Moodle. Úsala quien pregunte por sus notas actuales/de este semestre en general (no de una sola materia): Moodle suele tener los componentes evaluados antes de que el ERP publique la nota final, así que es más al día que record_academico/notas_por_semestre.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "carnet_estudiantil",
        "description": "Prepara la descarga del carne estudiantil (PDF). Usala cuando el estudiante pida, quiera o necesite su carne. No necesita parametros: la app muestra un boton de descarga debajo de tu respuesta.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "resumen_semana",
        "description": "Genera un resumen general de la semana: tareas pendientes + proximos eventos combinados.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOLS = TOOLS + plan_chat.HERRAMIENTAS

SYSTEM_PROMPT = """Eres AcademIA, un asistente academico para estudiantes universitarios.
Respondes preguntas sobre tareas, notas, calificaciones, eventos y horario,
usando UNICAMENTE los datos que obtienes de las herramientas disponibles.

Reglas importantes:
- NUNCA inventes una nota, fecha o dato que no venga de una herramienta.
- Si una herramienta indica que no hay datos sincronizados, dilo claramente
  al estudiante en vez de inventar una respuesta.
- Las tareas marcadas SIN VERIFICAR no son pendientes ni entregadas: no se sabe.
  Nunca digas que ya las entrego ni que le faltan; explica que no pudiste
  confirmarlo y que lo revise en Moodle. Solo llama "pendiente" a lo que la
  herramienta presenta como pendiente.
- Responde de forma breve, clara y en tono cercano (eres un asistente
  universitario, no un reporte formal).
- Si preguntan qué materias cursan o cómo van "ahora"/"este semestre"/"actualmente" SIN decir un
  número de semestre, usa materias_actuales (o rendimiento_actual para notas) directamente: la
  matrícula sincronizada ya dice qué está en_curso, no hace falta preguntar el semestre. Solo pregunta
  el semestre si piden explícitamente las notas de un semestre YA CURSADO (ej. "cómo pasé el 5to").
- Para notas "actuales"/"de este periodo" en general (no de una sola materia), prefiere
  rendimiento_actual sobre record_academico/notas_por_semestre: Moodle calcula los componentes
  (N1-N4, EXP1, EXP2, examen) antes de que el ERP publique la nota_final, así que suele estar más al
  día. Si el estudiante pide explícitamente "la nota del ERP" o la nota final/oficial, usa
  calificacion_materia o record_academico en su lugar.
- Si el estudiante pide su carne, usa la herramienta carnet_estudiantil y dile
  que pulse el boton "Descargar carne" que aparece debajo de tu mensaje. No
  describas el contenido del carne ni inventes sus datos.
- PLAN DE ESTUDIO (cuando el estudiante quiera organizar su tiempo, armar su plan u horario de estudio):
  1. Llama primero a ver_horario_personal para no repetir lo que ya sabes.
  2. Pregunta, de lunes a domingo, en que franjas PUEDE estudiar (pide horas exactas, ej. "de 18:00 a
     20:00"; si dice "en las tardes", pide el rango). Sus clases del ERP ya se descuentan solas.
  3. Pregunta que actividades hace en su dia a dia (trabajo, transporte, deporte, familia...) y a que
     horas, para no ponerle estudio encima.
  4. Haz UNA o DOS preguntas por mensaje, no un interrogatorio. NUNCA supongas ni inventes una hora.
     Para preguntas cerradas usa hacer_pregunta (botones): momento del dia (Mañana 7-12 / Tarde 12-18 /
     Noche 18-24), que hace a diario (varias=true: gimnasio, trabajo, transporte...), comidas (propon
     desayuno 07:00-07:30, almuerzo 13:00-14:00 y cena 19:30-20:30 como OPCIONES que acepta o corrige),
     dificultad de cada materia, o confirmar un resumen (Sí, guárdalo / Quiero cambiar algo). Luego pide
     horas exactas con texto cuando haga falta.
  5. Antes de guardar, resume lo entendido y pide confirmacion. Solo cuando confirme llama a
     guardar_franjas_libres y guardar_actividades.
  6. Despues llama a generar_plan_estudio y presenta el plan tal cual (las horas las calcula la app;
     no las cambies). Explica en una frase por que las materias con menor porcentaje en Moodle van
     primero y menciona los "no alcanza" y las tareas sin verificar si las hay.
  7. Dile que puede recibir recordatorios por Telegram desde la seccion Plan.
  8. Si pide mover un bloque, usa mover_bloque_estudio (queda fijado); para sumar rutina usa
     agregar_actividad (no borra lo demas); soltar_bloques_fijados devuelve el acomodo automatico.
- Si la pregunta no tiene relacion con tareas/notas/eventos/horario/carne
  academico, indica amablemente que solo puedes ayudar con esos temas.
"""


class AssistantLLM:
    """Chatbot con Claude y herramientas (tool calling). Mantiene el historial de UNA sesion; los datos salen siempre de QueryEngine y de las herramientas del plan, nunca de memoria del modelo."""
    def __init__(self, estudiante_id: int = 1, model: str = "claude-sonnet-4-6"):
        self.client = anthropic.Anthropic()  # usa ANTHROPIC_API_KEY del entorno
        self.model = model
        self.engine = QueryEngine(estudiante_id=estudiante_id)
        self.historial = []
        self.herramientas_usadas = []   # herramientas que uso en la ultima pregunta
        self.acciones = []              # botones que la interfaz debe mostrar (ej. "carnet")
        self.opciones = None            # pregunta con botones (hacer_pregunta) de la ultima respuesta
        self.lock = threading.Lock()    # dos preguntas a la vez de la misma sesion no mezclan el historial

    def _preparar_pregunta(self, args: dict) -> str:
        ops = [str(o).strip()[:60] for o in (args.get("opciones") or []) if str(o).strip()]
        pregunta = str(args.get("pregunta") or "").strip()[:200]
        if not pregunta or not 2 <= len(ops) <= 8:
            return "Error: la pregunta necesita un texto y de 2 a 8 opciones."
        self.opciones = {"pregunta": pregunta, "opciones": ops, "varias": bool(args.get("varias"))}
        return ("Listo: la interfaz mostrará los botones. Termina tu mensaje con esa pregunta y espera la respuesta; "
                "no llames otras herramientas en este turno.")

    def _ejecutar_herramienta(self, nombre: str, args: dict) -> str:
        if nombre == "hacer_pregunta":
            return self._preparar_pregunta(args or {})
        if nombre == "carnet_estudiantil":
            if "carnet" not in self.acciones:
                self.acciones.append("carnet")
            return ("Listo: la interfaz mostrara un boton 'Descargar carne' debajo de tu mensaje. "
                    "Dile al estudiante que lo pulse para bajar su carne en PDF.")
        if nombre in plan_chat.NOMBRES:
            try:
                resultado = plan_chat.ejecutar(nombre, self.engine.conn, self.engine.estudiante_id, args)
                if nombre not in ("ver_horario_personal",) and "horario" not in self.acciones:
                    self.acciones.append("horario")      # la interfaz muestra «Ver mi horario»
                return resultado
            except Exception as e:
                print(f"[ERROR en herramienta del plan '{nombre}']: {type(e).__name__}")
                return f"Error ejecutando {nombre}: {type(e).__name__}"
        metodo = getattr(self.engine, nombre, None)
        if not metodo:
            return f"Error: herramienta '{nombre}' no existe."
        try:
            return metodo(**args)
        except Exception as e:
            import traceback
            print(f"\n[ERROR interno en herramienta '{nombre}' con args {args}]")
            traceback.print_exc()
            print()
            return f"Error ejecutando {nombre}: {e}"

    def preguntar(self, pregunta: str) -> str:
        self.herramientas_usadas = []
        self.acciones = []
        self.opciones = None
        self.historial.append({"role": "user", "content": pregunta})

        while True:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=self.historial,
            )

            self.historial.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                # El modelo ya tiene lo que necesita para responder en texto
                texto = "".join(b.text for b in response.content if b.type == "text")
                return texto

            # El modelo pidio usar una o mas herramientas
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    if block.name not in self.herramientas_usadas:
                        self.herramientas_usadas.append(block.name)
                    resultado = self._ejecutar_herramienta(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(resultado),
                    })

            self.historial.append({"role": "user", "content": tool_results})

    def close(self):
        self.engine.close()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("Falta ANTHROPIC_API_KEY en .env - consiguela en console.anthropic.com")
        sys.exit(1)

    asistente = AssistantLLM()
    print("=== AcademIA (con IA real) ===")
    print("Escribe tu pregunta (o 'salir' para terminar)\n")

    while True:
        pregunta = input("Tú: ")
        if pregunta.lower() in ("salir", "exit", "quit"):
            break
        respuesta = asistente.preguntar(pregunta)
        print(f"Asistente: {respuesta}\n")

    asistente.close()

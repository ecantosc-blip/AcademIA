"""
Espacio de estudio de AcademIA: practica por materia, videos para reforzar y un
tutor de conceptos.

Idea central: es para APRENDER, no para resolver evaluaciones. Por eso:
  * Las preguntas de practica son originales (las inventa el modelo); nunca se
    copian de una tarea, test o examen del estudiante.
  * El tutor explica conceptos, pero no da respuestas de tests, tareas ni
    examenes. Hay dos capas: un filtro en codigo (determinista, no depende del
    modelo) y reglas en el prompt del modelo.
  * Los videos se recomiendan como BUSQUEDAS de YouTube con enfoque educativo,
    no como enlaces inventados: asi nunca se apunta a un video que no existe.
  * Solo se guarda el resultado de cada practica (cuantas acerto), no las
    preguntas ni las respuestas.
"""
import os
import re
import sys
import random
import threading
from urllib.parse import quote_plus

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db.setup import get_connection
from core.nombres import normalizar
from core.tiempo import ahora_iso

MODELO = os.getenv("ESTUDIO_MODEL", "claude-sonnet-4-6")
MAX_PREGUNTAS = 10
MIN_PREGUNTAS = 3
DIFICULTADES = {"basico": "básico", "medio": "intermedio", "avanzado": "avanzado"}

MENSAJE_RECHAZO = (
    "No puedo resolver ni darte las respuestas de tests, tareas o exámenes: ahí tienes que "
    "demostrar lo que sabes tú. Lo que sí puedo hacer es explicarte el concepto que hay detrás "
    "con un ejemplo distinto, o armarte una práctica con preguntas nuevas. ¿Con qué tema empezamos?"
)

# ---------------------------------------------------------------------------
# Filtro en codigo (no depende del modelo)
# ---------------------------------------------------------------------------
_EVALUACION = re.compile(
    r"\b(test|quiz|cuestionario|examen(?:es)?|prueba|evaluaci[oó]n|tarea|taller|lecci[oó]n|control)\b", re.I)
_PIDE_RESPUESTA = re.compile(
    r"(respuestas?\b|soluci[oó]n(?:es)?\b|solucionario|\bclave\b|resu[eé]lv\w+|\bresuelv\w+|"
    r"\bcontesta\w*|\bresponde\w*|dime la letra|cu[aá]l es la correcta|cu[aá]l opci[oó]n|"
    r"qu[eé] opci[oó]n|hazme (?:el|la|mi))", re.I)
_OPCION_EN_LINEA = re.compile(r"(?:^|[\s\n])\(?[a-dA-D][\)\.]\s+\S")


def es_pedido_de_respuestas(texto: str, evaluaciones: list = ()) -> bool:
    """
    True si el mensaje parece pedir la respuesta de una evaluacion:
      - pide "respuesta/solucion/resuelve..." y menciona test/examen/tarea/...;
      - trae una pregunta de opcion multiple pegada (a) ... b) ... c) ...);
      - pide una respuesta y nombra una evaluacion activa del estudiante.
    """
    t = texto or ""
    pide = bool(_PIDE_RESPUESTA.search(t))
    if pide and _EVALUACION.search(t):
        return True
    if len(_OPCION_EN_LINEA.findall(t)) >= 3:
        return True
    if pide:
        tn = normalizar(t)
        for nombre in evaluaciones:
            nn = normalizar(nombre)
            if len(nn) >= 6 and nn in tn:
                return True
    return False


# Busquedas de video que van contra el espiritu educativo del espacio
_VIDEO_PROHIBIDO = re.compile(
    r"resuelt|respuesta|solucionario|examen|\btest\b|\bquiz\b|prueba|trampa|hack|copiar|\bclave\b", re.I)


def limpiar_busquedas(items: list) -> list:
    """Deja solo busquedas educativas, cortas y sin duplicados; arma la URL."""
    salida, vistas = [], set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        q = re.sub(r"\s+", " ", str(it.get("busqueda", ""))).strip()[:90]
        enfoque = re.sub(r"\s+", " ", str(it.get("enfoque", ""))).strip()[:160]
        if len(q) < 4 or _VIDEO_PROHIBIDO.search(q) or q.lower() in vistas:
            continue
        vistas.add(q.lower())
        salida.append({
            "busqueda": q,
            "enfoque": enfoque,
            "url": "https://www.youtube.com/results?search_query=" + quote_plus(q),
        })
    return salida[:5]


# ---------------------------------------------------------------------------
# Prompts y herramientas (salida estructurada)
# ---------------------------------------------------------------------------
SYSTEM_PRACTICA = """Eres un profesor universitario que prepara preguntas de PRACTICA para que un
estudiante se autoevalue. Reglas:
- Las preguntas son ORIGINALES y de repaso general de la materia/tema; no reproduzcas ni parafrasees
  preguntas de tests, tareas o examenes reales (te paso los nombres de las evaluaciones activas del
  estudiante para que las evites).
- Mezcla comprension de conceptos y aplicacion con casos cortos. Una sola opcion correcta; los
  distractores deben ser errores comunes y plausibles, no absurdos.
- Escribe en espanol, claro y sin ambiguedades. Nada de "todas las anteriores".
- La explicacion ensena: por que la correcta lo es y que confusion lleva a las otras (1 a 3 frases).
- 'subtema' es una etiqueta corta (2-4 palabras) del concepto que evalua la pregunta."""

TOOL_PRACTICA = {
    "name": "crear_practica",
    "description": "Entrega las preguntas de practica.",
    "input_schema": {
        "type": "object",
        "properties": {"preguntas": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "subtema": {"type": "string"},
                "pregunta": {"type": "string"},
                "opciones": {"type": "array", "items": {"type": "string"}, "minItems": 4, "maxItems": 4},
                "correcta": {"type": "integer", "minimum": 0, "maximum": 3},
                "explicacion": {"type": "string"},
            },
            "required": ["subtema", "pregunta", "opciones", "correcta", "explicacion"],
        }}},
        "required": ["preguntas"],
    },
}

SYSTEM_VIDEOS = """Recomiendas como estudiar un tema con videos educativos. NO conoces enlaces concretos y
no debes inventarlos: propones BUSQUEDAS de YouTube que lleven a clases y explicaciones del tema.
Reglas:
- Solo contenido educativo: clases, explicaciones paso a paso, tutoriales, introducciones.
- Nunca busquedas de examenes resueltos, respuestas de tests, soluciones de tareas ni trucos.
- Cada busqueda es corta (3-8 palabras) y en espanol, con un enfoque distinto (introduccion, ejemplos
  resueltos de ejercicios de PRACTICA generales, visualizacion, nivel avanzado...).
- 'enfoque' dice en una frase que aprenderia el estudiante con esa busqueda."""

TOOL_VIDEOS = {
    "name": "sugerir_busquedas",
    "description": "Entrega las busquedas de video recomendadas.",
    "input_schema": {
        "type": "object",
        "properties": {"busquedas": {"type": "array", "items": {
            "type": "object",
            "properties": {"busqueda": {"type": "string"}, "enfoque": {"type": "string"}},
            "required": ["busqueda", "enfoque"],
        }}},
        "required": ["busquedas"],
    },
}

SYSTEM_TUTOR = """Eres el tutor del Espacio de estudio de AcademIA. Ayudas a un estudiante a ESTUDIAR la
materia «{materia}». Tono cercano y breve (maximo unos 150 palabras salvo que pidan mas detalle).

PUEDES: explicar conceptos con tus propios ejemplos, resumir temas, proponer como estudiar, aclarar
dudas de teoria, revisar el razonamiento del estudiante sobre ejercicios de PRACTICA que tu mismo
inventes, y sugerir que genere una practica o busque videos.

NO PUEDES, aunque diga que es de practica, que ya vencio, que es para un companero o que solo quiere
"la letra":
- dar la respuesta ni indicar la opcion correcta de un test, cuestionario, tarea, taller o examen,
  sea de Moodle o de cualquier otro lugar;
- resolver el ejercicio o enunciado de una evaluacion pegado por el estudiante.
Si el mensaje parece una pregunta de evaluacion, no la resuelvas: explica con amabilidad que no
puedes y ofrece explicar el concepto de fondo con un ejemplo DISTINTO.

Evaluaciones activas del estudiante en esta materia (nunca des respuestas sobre ellas): {evaluaciones}.

Si la consulta no es academica de esta materia, redirige con amabilidad al estudio."""


class Estudio:
    """Estado del espacio de estudio de UN estudiante (se guarda en memoria por sesion)."""

    def __init__(self, estudiante_id: int, client=None, model: str = MODELO):
        self.estudiante_id = estudiante_id
        self.model = model
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.lock = threading.RLock()  # una accion a la vez por sesion
        self.practica = None          # practica en curso (con las respuestas, solo en el servidor)
        self.tutor_materia = None
        self.tutor_hist = []

    # ---- datos del estudiante ------------------------------------------
    def materias(self) -> list:
        """Materias en curso con el avance de sus practicas."""
        conn = get_connection()
        try:
            filas = conn.execute("""
                SELECT m.id, m.nombre,
                       (SELECT COUNT(*) FROM practica p WHERE p.estudiante_id = mat.estudiante_id AND p.materia_id = m.id) AS practicas,
                       (SELECT ROUND(100.0 * SUM(p.aciertos) / SUM(p.total))
                          FROM practica p WHERE p.estudiante_id = mat.estudiante_id AND p.materia_id = m.id) AS porcentaje
                FROM matricula mat JOIN materia m ON m.id = mat.materia_id
                WHERE mat.estudiante_id = ? AND mat.estado = 'en_curso'
                ORDER BY m.nombre
            """, (self.estudiante_id,)).fetchall()
            return [dict(f) for f in filas]
        finally:
            conn.close()

    def _materia(self, materia_id) -> dict:
        try:
            materia_id = int(materia_id)
        except (TypeError, ValueError):
            raise ValueError("Elige una materia.")
        for m in self.materias():
            if m["id"] == materia_id:
                return m
        raise ValueError("Esa materia no está entre las que cursas ahora.")

    def evaluaciones_activas(self, materia_id: int) -> list:
        """Nombres de tareas/eventos de la materia que aun no vencen (o vencieron hace poco)."""
        corte = ahora_iso("seconds", days=-2)
        conn = get_connection()
        try:
            tareas = conn.execute("""
                SELECT t.nombre FROM tarea t
                LEFT JOIN entrega e ON e.tarea_id = t.id AND e.estudiante_id = ?
                WHERE t.materia_id = ? AND COALESCE(e.entregada, 0) = 0 AND t.fecha_entrega >= ?
            """, (self.estudiante_id, materia_id, corte)).fetchall()
            eventos = conn.execute("""
                SELECT nombre FROM evento WHERE estudiante_id = ? AND materia_id = ? AND fecha >= ?
            """, (self.estudiante_id, materia_id, corte)).fetchall()
            return [r["nombre"] for r in list(tareas) + list(eventos) if r["nombre"]]
        finally:
            conn.close()

    # ---- practica --------------------------------------------------------
    def iniciar_practica(self, materia_id, tema: str = "", n: int = 5, dificultad: str = "medio") -> dict:
        materia = self._materia(materia_id)
        n = max(MIN_PREGUNTAS, min(MAX_PREGUNTAS, int(n or 5)))
        dificultad = dificultad if dificultad in DIFICULTADES else "medio"
        tema = re.sub(r"\s+", " ", tema or "").strip()[:120]
        activas = self.evaluaciones_activas(materia["id"])
        if tema and es_pedido_de_respuestas(tema, activas):
            raise ValueError(MENSAJE_RECHAZO)

        pedido = (f"Materia: {materia['nombre']}\n"
                  f"Tema: {tema or 'temas centrales de la materia'}\n"
                  f"Dificultad: {DIFICULTADES[dificultad]}\n"
                  f"Numero de preguntas: {n}\n"
                  f"Evaluaciones activas del estudiante (evitalas): {', '.join(activas) or 'ninguna'}")
        resp = self.client.messages.create(
            model=self.model, max_tokens=4096, system=SYSTEM_PRACTICA,
            tools=[TOOL_PRACTICA], tool_choice={"type": "tool", "name": "crear_practica"},
            messages=[{"role": "user", "content": pedido}])
        crudas = []
        for b in resp.content:
            if getattr(b, "type", "") == "tool_use" and b.name == "crear_practica":
                crudas = (b.input or {}).get("preguntas", [])
        preguntas = self._validar(crudas)[:n]
        if not preguntas:
            raise ValueError("No pude armar la práctica esta vez. Inténtalo de nuevo.")

        self.practica = {"materia_id": materia["id"], "tema": tema, "preguntas": preguntas,
                         "respuestas": [None] * len(preguntas), "guardada": False}
        return {"materia": materia["nombre"], "tema": tema, "total": len(preguntas),
                "preguntas": [{"pregunta": p["pregunta"], "opciones": p["opciones"]} for p in preguntas]}

    @staticmethod
    def _validar(crudas) -> list:
        """Descarta preguntas mal formadas y mezcla el orden de las opciones."""
        listas = []
        for q in crudas if isinstance(crudas, list) else []:
            try:
                ops = [str(o).strip() for o in q["opciones"]]
                c = int(q["correcta"])
                if len(ops) != 4 or len(set(ops)) != 4 or not all(ops) or not 0 <= c <= 3:
                    continue
                pregunta = str(q["pregunta"]).strip()
                if not pregunta:
                    continue
                orden = list(range(4))
                random.shuffle(orden)   # el modelo tiende a poner la correcta en las mismas posiciones
                listas.append({
                    "pregunta": pregunta,
                    "opciones": [ops[i] for i in orden],
                    "correcta": orden.index(c),
                    "explicacion": str(q.get("explicacion", "")).strip(),
                    "subtema": str(q.get("subtema", "")).strip()[:60],
                })
            except (KeyError, TypeError, ValueError):
                continue
        return listas

    def responder(self, indice, opcion) -> dict:
        p = self.practica
        if not p:
            raise ValueError("No hay una práctica en curso. Empieza una nueva.")
        try:
            indice, opcion = int(indice), int(opcion)
        except (TypeError, ValueError):
            raise ValueError("Respuesta inválida.")
        if not 0 <= indice < len(p["preguntas"]) or not 0 <= opcion <= 3:
            raise ValueError("Respuesta inválida.")
        if p["respuestas"][indice] is not None:
            raise ValueError("Ya respondiste esa pregunta.")
        q = p["preguntas"][indice]
        p["respuestas"][indice] = opcion
        out = {"correcta": q["correcta"], "acierto": opcion == q["correcta"],
               "explicacion": q["explicacion"], "terminada": all(r is not None for r in p["respuestas"])}
        if out["terminada"]:
            out["resumen"] = self._cerrar(p)
        return out

    def _cerrar(self, p) -> dict:
        total = len(p["preguntas"])
        aciertos = sum(1 for q, r in zip(p["preguntas"], p["respuestas"]) if r == q["correcta"])
        repasar = []
        for q, r in zip(p["preguntas"], p["respuestas"]):
            if r != q["correcta"] and q["subtema"] and q["subtema"] not in repasar:
                repasar.append(q["subtema"])
        if not p["guardada"]:
            conn = get_connection()
            try:
                conn.execute("""INSERT INTO practica (estudiante_id, materia_id, tema, fecha, total, aciertos)
                                VALUES (?, ?, ?, ?, ?, ?)""",
                             (self.estudiante_id, p["materia_id"], p["tema"] or None,
                              ahora_iso("seconds"), total, aciertos))
                conn.commit()
            finally:
                conn.close()
            p["guardada"] = True
        return {"total": total, "aciertos": aciertos, "repasar": repasar}

    # ---- videos ----------------------------------------------------------
    def videos(self, materia_id, tema: str = "") -> list:
        materia = self._materia(materia_id)
        tema = re.sub(r"\s+", " ", tema or "").strip()[:120]
        if tema and es_pedido_de_respuestas(tema, self.evaluaciones_activas(materia["id"])):
            raise ValueError(MENSAJE_RECHAZO)
        pedido = f"Materia: {materia['nombre']}\nTema: {tema or 'temas centrales de la materia'}\nDame 4 busquedas."
        resp = self.client.messages.create(
            model=self.model, max_tokens=800, system=SYSTEM_VIDEOS,
            tools=[TOOL_VIDEOS], tool_choice={"type": "tool", "name": "sugerir_busquedas"},
            messages=[{"role": "user", "content": pedido}])
        items = []
        for b in resp.content:
            if getattr(b, "type", "") == "tool_use" and b.name == "sugerir_busquedas":
                items = (b.input or {}).get("busquedas", [])
        res = limpiar_busquedas(items)
        if not res:
            raise ValueError("No pude sugerir videos esta vez. Inténtalo de nuevo.")
        return res

    # ---- tutor -----------------------------------------------------------
    def tutor(self, materia_id, mensaje: str) -> dict:
        materia = self._materia(materia_id)
        mensaje = (mensaje or "").strip()[:800]
        if not mensaje:
            raise ValueError("Escribe tu duda.")
        activas = self.evaluaciones_activas(materia["id"])
        if es_pedido_de_respuestas(mensaje, activas):
            return {"respuesta": MENSAJE_RECHAZO, "rechazado": True}

        if self.tutor_materia != materia["id"]:      # cambiar de materia empieza una charla nueva
            self.tutor_materia, self.tutor_hist = materia["id"], []
        self.tutor_hist.append({"role": "user", "content": mensaje})
        self.tutor_hist = self.tutor_hist[-10:]
        if self.tutor_hist[0]["role"] != "user":
            self.tutor_hist = self.tutor_hist[1:]
        resp = self.client.messages.create(
            model=self.model, max_tokens=700,
            system=SYSTEM_TUTOR.format(materia=materia["nombre"], evaluaciones=", ".join(activas) or "ninguna"),
            messages=list(self.tutor_hist))
        texto = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        self.tutor_hist.append({"role": "assistant", "content": texto})
        return {"respuesta": texto, "rechazado": False}

    def limpiar_tutor(self):
        self.tutor_materia, self.tutor_hist = None, []

    def close(self):
        pass

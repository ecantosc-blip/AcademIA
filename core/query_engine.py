"""
Motor de consultas - version 1 (basada en reglas simples).

Objetivo de esta primera version: validar que el flujo completo
(pregunta -> consulta a la BD -> respuesta) funciona de punta a punta,
ANTES de invertir tiempo integrando un LLM. Una vez que esto funcione,
se reemplaza la deteccion de intencion por un LLM y se usa esta misma
capa de acceso a datos como las "herramientas" que el LLM puede invocar
(patron RAG / tool-calling).
"""
import os
import threading
import sys
import unicodedata

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.setup import get_connection
from core.tareas import clasificar_tareas
from core.nombres import coincide
from core import notas_moodle


def _normalizar(texto: str) -> str:
    """Quita acentos y pasa a minusculas, para que 'automatizacion' y
    'automatización' se traten como el mismo texto al buscar coincidencias."""
    if not texto:
        return ""
    sin_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sin_acentos.lower()


def _plural_tareas(n: int) -> str:
    return f"{n} tarea{'s' if n != 1 else ''} vencida{'s' if n != 1 else ''} sin entregar" if n else "0 tareas vencidas"


# Una conexion por HILO del servidor (no una por estudiante): con cientos de
# sesiones abiertas, una conexion por sesion agotaria los descriptores de
# archivo. Los hilos son pocos y fijos, asi que las conexiones quedan acotadas.
_hilo = threading.local()


class QueryEngine:
    """Consultas de solo lectura sobre los datos ya sincronizados de UN estudiante (SQLite). Es la unica fuente de datos del chatbot: la IA nunca inventa cifras, las pide aqui."""
    def __init__(self, estudiante_id: int = 1):
        self.estudiante_id = estudiante_id

    @property
    def conn(self):
        c = getattr(_hilo, "conn", None)
        if c is None:
            c = _hilo.conn = get_connection()
        return c

    # --- Funcionalidad 1: tareas pendientes (todas las materias) ---
    def tareas_pendientes(self):
        return self._texto_tareas(clasificar_tareas(self.conn, self.estudiante_id))

    # --- Funcionalidad 2: tareas de una materia especifica ---
    def tareas_por_materia(self, nombre_materia: str):
        materia = self._buscar_materia(nombre_materia)
        if not materia:
            return f"No encontré ninguna materia que coincida con '{nombre_materia}'."
        return self._texto_tareas(clasificar_tareas(self.conn, self.estudiante_id, materia_id=materia["id"]),
                                  materia=materia["nombre"])

    @staticmethod
    def _texto_tareas(g: dict, materia: str = None) -> str:
        """Texto para el chat. Usa el criterio unico de core/tareas.py: lo que Moodle no
        informo se presenta como 'sin verificar', nunca como pendiente ni como entregada."""
        def linea(t):
            donde = "" if materia else f" ({t['materia']})"
            vence = t["fecha"] if t["fecha"] else "sin fecha"
            extra = " [la guardaste como borrador, falta enviarla]" if t["estado"] == "borrador" else ""
            return f"- {t['tarea']}{donde} - vence: {vence}{extra}"

        futuras_sv = [t for t in g["sin_verificar"] if not t["vencida"]]
        pasadas_sv = [t for t in g["sin_verificar"] if t["vencida"]]
        partes = []
        if g["pendientes"]:
            titulo = f"Tareas pendientes de {materia}" if materia else "Tus tareas pendientes"
            partes.append(f"{titulo} (Moodle confirma que no las has entregado):\n" + "\n".join(map(linea, g["pendientes"])))
        if g["vencidas_sin_entregar"]:
            partes.append("Vencidas sin entregar (Moodle confirma que no las entregaste):\n"
                          + "\n".join(map(linea, g["vencidas_sin_entregar"])))
        if g["sin_verificar"]:
            bloque = ["SIN VERIFICAR: no pude confirmar si ya las entregaste porque Moodle no me informó "
                      "su estado de entrega. No las cuento ni como pendientes ni como entregadas; revísalas en Moodle."]
            bloque += [linea(t) for t in futuras_sv]
            if pasadas_sv:
                bloque.append(f"- Además, {len(pasadas_sv)} tarea(s) con fecha ya pasada de las que tampoco tengo el estado.")
            partes.append("\n".join(bloque))
        if not partes:
            return (f"No tienes tareas pendientes registradas en {materia}." if materia
                    else "No tienes tareas pendientes registradas.")
        return "\n\n".join(partes)

    # --- Funcionalidad 4: proximos eventos ---
    def proximos_eventos(self, limite: int = 5):
        cur = self.conn.execute("""
            SELECT nombre, tipo, fecha FROM evento
            WHERE estudiante_id = ?
            ORDER BY fecha ASC LIMIT ?
        """, (self.estudiante_id, limite))
        rows = cur.fetchall()
        if not rows:
            return "No hay eventos próximos registrados."
        lineas = [f"- {r['nombre']} ({r['tipo']}) - {r['fecha']}" for r in rows]
        return "Tus próximos eventos:\n" + "\n".join(lineas)

    # --- Funcionalidad 5: horario ---
    def horario_materia(self, nombre_materia: str = None):
        """Horario de clases de una materia (o de todas si no se indica) en texto legible."""
        materia_id = None
        if nombre_materia:
            materia = self._buscar_materia(nombre_materia)
            if not materia:
                return f"No encontré ninguna materia que coincida con '{nombre_materia}'."
            materia_id = materia["id"]

        query = """
            SELECT DISTINCT m.nombre AS materia, h.dia, h.hora_inicio, h.hora_fin, h.aula
            FROM horario h
            JOIN materia m ON m.id = h.materia_id
            JOIN matricula mat ON mat.materia_id = m.id AND mat.estudiante_id = ?
        """
        params = [self.estudiante_id]
        if materia_id:
            query += " WHERE h.materia_id = ?"
            params.append(materia_id)
        cur = self.conn.execute(query, params)
        rows = cur.fetchall()
        if not rows:
            return "No encontré horario registrado. Usa el botón de sincronizar (⟳) en la parte superior de la página."
        lineas = [f"- {r['materia']}: {r['dia']} {r['hora_inicio']}-{r['hora_fin']} (Aula {r['aula']})" for r in rows]
        return "Horario:\n" + "\n".join(lineas)

    # --- Funcionalidad 9: resumen semanal (combina tareas + eventos) ---
    def resumen_semana(self):
        tareas = self.tareas_pendientes()
        eventos = self.proximos_eventos(limite=5)
        return f"Resumen de tu semana:\n\n{tareas}\n\n{eventos}"

    # --- Router simple de intencion (reemplazable por LLM despues) ---
    def responder(self, pregunta: str) -> str:
        """Enrutador sencillo por palabras clave para preguntas frecuentes, sin llamar a la IA. Devuelve texto."""
        p = pregunta.lower()

        if "resumen" in p or "esta semana" in p or "qué tengo" in p:
            return self.resumen_semana()

        if "necesito" in p or "me falta" in p or "para aprobar" in p:
            materia = self._extraer_materia(p)
            if materia:
                return self.nota_necesaria(materia)
            return "¿De qué materia quieres saber cuánto te falta para aprobar?"

        if "peor" in p or "más baja" in p or "mas baja" in p:
            return self.comparar_materias(peor=True)
        if "mejor" in p or "más alta" in p or "mas alta" in p:
            return self.comparar_materias(peor=False)

        if "detalle" in p or "componentes" in p:
            materia = self._extraer_materia(p)
            if materia:
                return self.calificaciones_detalladas(materia)
            return "¿De qué materia quieres ver el detalle de calificaciones?"

        if "nota" in p or "calificaci" in p or "cómo voy" in p or "como voy" in p:
            materia = self._extraer_materia(p)
            if materia:
                return self.calificacion_materia(materia)
            return "¿De qué materia quieres saber tu nota?"

        if "tarea" in p:
            materia = self._extraer_materia(p)
            if materia:
                return self.tareas_por_materia(materia)
            return self.tareas_pendientes()

        if "examen" in p or "evento" in p or "próximo" in p or "proximo" in p:
            return self.proximos_eventos()

        if "horario" in p or "clase" in p:
            materia = self._extraer_materia(p)
            return self.horario_materia(materia)

        return ("Aún no sé responder eso con las reglas actuales. "
                "Preguntas soportadas: tareas, notas, calificaciones detalladas, "
                "comparar materias, nota necesaria para aprobar, próximos eventos, "
                "horario, resumen semanal.")

    def _buscar_materia(self, nombre_query: str):
        """
        Busca la materia real en la base de datos que mejor coincide con
        el texto recibido, ignorando acentos, mayusculas y coincidencias
        parciales. Se usa en TODOS los metodos que reciben un nombre de
        materia (incluidas las herramientas que llama el LLM directamente),
        para que no falle por diferencias como 'automatizacion' vs
        'automatización'.

        IMPORTANTE: la busqueda se restringe a las materias en las que
        ESTE estudiante esta matriculado (via matricula), no a la tabla
        materia completa (que es compartida entre todos los usuarios) -
        de lo contrario, un estudiante podria terminar viendo datos de
        una materia que en realidad pertenece a otro usuario con nombre
        parecido.
        Devuelve una fila (id, nombre) o None si no encuentra nada.
        """
        if not nombre_query:
            return None

        query_norm = _normalizar(nombre_query)
        cur = self.conn.execute("""
            SELECT DISTINCT m.id, m.nombre
            FROM materia m
            JOIN matricula mat ON mat.materia_id = m.id
            WHERE mat.estudiante_id = ?
        """, (self.estudiante_id,))
        materias = cur.fetchall()

        mejor = None
        mejor_longitud = 0
        for m in materias:
            if not m["nombre"]:
                continue
            nombre_norm = _normalizar(m["nombre"])
            if nombre_norm == query_norm:
                return m
            # Coincidencia en cualquier direccion pero por palabras completas
            # ('programacion i' no es 'programacion ii').
            if coincide(nombre_norm, query_norm) and len(nombre_norm) > mejor_longitud:
                mejor = m
                mejor_longitud = len(nombre_norm)
        return mejor

    def _extraer_materia(self, texto: str):
        """
        Busca, entre las materias registradas, cual coincide con la pregunta.
        Hace match parcial en ambas direcciones e ignora acentos/mayusculas
        (usa _buscar_materia). Se mantiene esta funcion como punto de
        entrada para el router de reglas, que recibe la pregunta completa.
        """
        row = self._buscar_materia(texto)
        return row["nombre"] if row else None

    # --- Calificaciones (usa el dato oficial del SGA: nota_final) ---
    def calificacion_materia(self, nombre_materia: str):
        materia = self._buscar_materia(nombre_materia)
        if not materia:
            return f"No encontré ninguna materia que coincida con '{nombre_materia}'."

        cur = self.conn.execute("""
            SELECT nota_final, asistencia FROM matricula
            WHERE estudiante_id = ? AND materia_id = ?
        """, (self.estudiante_id, materia["id"]))
        row = cur.fetchone()
        if not row or row["nota_final"] is None:
            return f"No tengo la nota de {materia['nombre']} sincronizada todavía. Usa el botón de sincronizar (⟳) en la parte superior de la página."
        return (f"En {materia['nombre']} llevas una nota de {row['nota_final']} "
                f"y {row['asistencia']}% de asistencia.")

    def calificaciones_detalladas(self, nombre_materia: str):
        """Desglose de notas por componente de una materia, tal como lo entrega Moodle."""
        materia = self._buscar_materia(nombre_materia)
        if not materia:
            return f"No encontré ninguna materia que coincida con '{nombre_materia}'."

        cur = self.conn.execute("""
            SELECT cn.nombre_item, cn.calificacion, cn.porcentaje
            FROM componente_nota cn
            JOIN matricula mat ON mat.id = cn.matricula_id
            WHERE mat.estudiante_id = ? AND mat.materia_id = ?
        """, (self.estudiante_id, materia["id"]))
        rows = cur.fetchall()
        if not rows:
            return f"No tengo calificaciones detalladas de {materia['nombre']} sincronizadas todavía."
        lineas = [f"- {r['nombre_item']}: {r['calificacion']} ({r['porcentaje']})" for r in rows]
        return f"Calificaciones detalladas de {materia['nombre']}:\n" + "\n".join(lineas)

    # --- Comparación entre materias (¿en cuál voy peor/mejor?) ---
    def comparar_materias(self, peor: bool = True):
        """Materias con la mejor (peor=False) o la peor (peor=True) nota final registrada."""
        cur = self.conn.execute("""
            SELECT m.nombre AS materia, mat.nota_final
            FROM matricula mat
            JOIN materia m ON m.id = mat.materia_id
            WHERE mat.estudiante_id = ? AND mat.nota_final IS NOT NULL
            ORDER BY mat.nota_final ASC
        """, (self.estudiante_id,))
        rows = cur.fetchall()
        if not rows:
            return "No tengo notas sincronizadas todavía para comparar."

        elegido = rows[0] if peor else rows[-1]
        etiqueta = "más baja" if peor else "más alta"
        detalle = ", ".join(f"{r['materia']}: {r['nota_final']}" for r in rows)
        return (f"Tu nota {etiqueta} es en {elegido['materia']} ({elegido['nota_final']}).\n"
                f"Todas tus notas: {detalle}")

    # --- Simulador de nota necesaria para aprobar ---
    # NOTA: usa 70 como nota minima de aprobacion por defecto (el valor visto
    # en el modelo evaluativo de ejemplo durante las pruebas). Esto es una
    # aproximacion: el valor real de "notaaprobar" varia por modelo evaluativo
    # y todavia no se extrae automaticamente de la API - queda documentado
    # como mejora pendiente.
    def nota_necesaria(self, nombre_materia: str, nota_minima_aprobar: float = 70.0):
        """Cuanto necesita sacar el estudiante en lo que falta para llegar a la nota minima de aprobacion (por defecto 70)."""
        materia = self._buscar_materia(nombre_materia)
        if not materia:
            return f"No encontré ninguna materia que coincida con '{nombre_materia}'."

        cur = self.conn.execute("""
            SELECT nota_final FROM matricula
            WHERE estudiante_id = ? AND materia_id = ?
        """, (self.estudiante_id, materia["id"]))
        row = cur.fetchone()
        if not row or row["nota_final"] is None:
            return f"No tengo la nota actual de {materia['nombre']} para calcular esto. Usa el botón de sincronizar (⟳) en la parte superior de la página."

        faltante = nota_minima_aprobar - row["nota_final"]
        if faltante <= 0:
            return f"Ya tienes {row['nota_final']} en {materia['nombre']} - alcanzaste el mínimo para aprobar ({nota_minima_aprobar})."
        return (f"Tienes {row['nota_final']} en {materia['nombre']}. "
                f"Te faltan {faltante:.1f} puntos para llegar a {nota_minima_aprobar} "
                f"(nota mínima de aprobación asumida - verifica el valor real en el SGA para tu materia).")

    # --- Profesor de una materia ---
    def profesor_materia(self, nombre_materia: str):
        """Docentes asignados a una materia."""
        materia = self._buscar_materia(nombre_materia)
        if not materia:
            return f"No encontré ninguna materia que coincida con '{nombre_materia}'."

        cur = self.conn.execute("""
            SELECT d.nombre, d.email
            FROM materia_docente md
            JOIN docente d ON d.id = md.docente_id
            WHERE md.materia_id = ?
            ORDER BY d.nombre
        """, (materia["id"],))
        docentes = cur.fetchall()
        if not docentes:
            return f"No tengo registrado el profesor de {materia['nombre']} todavía."

        nombres = ", ".join(d["nombre"] for d in docentes)
        correos = ", ".join(d["email"] for d in docentes if d["email"])
        texto = (f"El profesor de {materia['nombre']} es {nombres}." if len(docentes) == 1
                 else f"Los profesores de {materia['nombre']} son {nombres}.")
        if correos:
            texto += f" Correo: {correos}"
        else:
            texto += " (el correo no está disponible públicamente en el sistema)."
        return texto

    # --- Historial academico completo (todos los semestres) ---
    def materias_aprobadas(self):
        cur = self.conn.execute("""
            SELECT m.nombre AS materia, mat.periodo, mat.nota_final
            FROM matricula mat
            JOIN materia m ON m.id = mat.materia_id
            WHERE mat.estudiante_id = ? AND mat.estado = 'aprobada'
            ORDER BY mat.periodo
        """, (self.estudiante_id,))
        rows = cur.fetchall()
        if not rows:
            return "No tengo tu historial de materias aprobadas sincronizado todavía."
        lineas = [f"- {r['materia']} ({r['periodo']}) - Nota: {r['nota_final']}" for r in rows]
        return f"Materias aprobadas ({len(rows)}):\n" + "\n".join(lineas)

    def record_academico(self):
        """Historial de materias del estudiante (periodo, estado y nota final)."""
        cur = self.conn.execute("""
            SELECT m.nombre AS materia, mat.periodo, mat.estado, mat.nota_final
            FROM matricula mat
            JOIN materia m ON m.id = mat.materia_id
            WHERE mat.estudiante_id = ?
            ORDER BY mat.periodo, m.nombre
        """, (self.estudiante_id,))
        rows = cur.fetchall()
        if not rows:
            return "No tengo tu registro académico sincronizado todavía."

        total = len(rows)
        aprobadas = sum(1 for r in rows if r["estado"] == "aprobada")
        reprobadas = sum(1 for r in rows if r["estado"] == "reprobada")

        lineas = [f"- {r['materia']} ({r['periodo']}) - {r['estado'].upper()} - Nota: {r['nota_final']}" for r in rows]
        resumen = f"Registro académico ({total} materias cursadas, {aprobadas} aprobadas, {reprobadas} reprobadas):\n"
        return resumen + "\n".join(lineas)

    # --- Rendimiento ponderado por componente (N1, N2, N3, N4, EXP1, EXP2, examen) ---
    # Pesos del modelo evaluativo institucional (confirmado por el usuario):
    # N1+N2+N3+N4 = 40 puntos (10 c/u), EXP1+EXP2 = 30 puntos (15 c/u),
    # examen final = 30 puntos. Total = 100.
    # NOTA: este modelo de pesos puede variar segun la materia/carrera -
    # se documenta como una asuncion, igual que en nota_necesaria().
    # LIMITE CONOCIDO: en algunas materias Moodle no divide la practica en "EXP1"/"EXP2" sino que
    # usa una sola categoria "EXP" (visto en un caso real). Como "EXP" no esta en este diccionario,
    # ese bloque queda fuera del calculo (ni suma a lo obtenido ni a lo posible) en vez de inventarle
    # un peso. Pendiente: confirmar si en esos casos "EXP" vale 15 o 30 antes de agregarlo aqui.
    PESOS_COMPONENTES = {
        "N1": 10, "N2": 10, "N3": 10, "N4": 10,
        "EXP1": 15, "EXP2": 15,
        "EX": 30, "EXT": 30,  # examen final (la etiqueta exacta puede variar)
    }

    def _componentes_de(self, materia_id: int):
        """Detalle y totales ponderados (segun PESOS_COMPONENTES) de una materia, por su materia_id.
        Devuelve (detalle: list[str], total_obtenido: float, total_posible: float). detalle vacio si
        no hay componentes con peso conocido sincronizados.

        IMPORTANTE: Moodle no entrega el total de cada bloque como "N1", "N2"... sino como
        "Total N1", "Total N2" (ver moodle/client.py::get_grades_table_rows, confirmado con
        pantallazos reales del SGA). Antes esta funcion comparaba el nombre_item completo contra
        PESOS_COMPONENTES (que solo tiene "N1", "N2"...), asi que NUNCA encontraba una coincidencia
        y rendimiento_materia()/rendimiento_actual() siempre decian "sin componentes sincronizados"
        aunque Moodle sí tuviera notas. Se quita el prefijo "Total " (sin importar mayusculas) antes
        de comparar, igual que ya hacia web/panel.py al mostrar estos mismos datos."""
        cur = self.conn.execute("""
            SELECT cn.nombre_item, cn.calificacion
            FROM componente_nota cn
            JOIN matricula mat ON mat.id = cn.matricula_id
            WHERE mat.estudiante_id = ? AND mat.materia_id = ?
        """, (self.estudiante_id, materia_id))
        detalle = []
        total_obtenido = 0.0
        total_posible = 0.0
        for r in cur.fetchall():
            etiqueta = (r["nombre_item"] or "").strip()
            if etiqueta[:6].lower() == "total ":
                etiqueta = etiqueta[6:].strip()
            etiqueta = etiqueta.upper()
            peso = self.PESOS_COMPONENTES.get(etiqueta)
            if not peso:
                continue

            try:
                calif_num = float(str(r["calificacion"]).replace(",", "."))
            except (ValueError, TypeError):
                calif_num = None

            total_posible += peso
            if calif_num is not None:
                # "Total N1"/"Total EXP1"/"Total EX"... es el total que Moodle YA calculo para ese
                # bloque, ponderado sobre su propio peso (no una nota de 0 a 10 que haya que
                # reescalar). Se confirmo con pantallazos reales del SGA: en una materia donde solo
                # "Total EX" (peso 30) tenia nota (27,73) y el resto estaba sin calificar, "Total del
                # curso" tambien era 27,73 - es decir, Moodle ya lo entrega en la escala del peso, no
                # sobre 10. Antes este codigo reescalaba igual (calif/10*peso), lo que inflaba
                # cualquier componente con peso distinto de 10 (ej. "Total EXP1"=15,00 -> se convertia
                # en 22.5/15, mas de lo maximo posible). Se usa el valor tal cual, limitado al peso
                # por seguridad ante un dato inesperado de Moodle.
                obtenido = min(calif_num, peso)
                total_obtenido += obtenido
                detalle.append(f"- {etiqueta}: {obtenido:.1f}/{peso} pts")
            else:
                detalle.append(f"- {etiqueta}: sin calificar (vale {peso} pts)")
        return detalle, total_obtenido, total_posible

    def rendimiento_materia(self, nombre_materia: str):
        """Resumen del rendimiento en una materia: nota, asistencia y componentes evaluados."""
        materia = self._buscar_materia(nombre_materia)
        if not materia:
            return f"No encontré ninguna materia que coincida con '{nombre_materia}'."

        detalle, total_obtenido, total_posible = self._componentes_de(materia["id"])
        if not detalle:
            cur = self.conn.execute("""
                SELECT 1 FROM componente_nota cn JOIN matricula mat ON mat.id = cn.matricula_id
                WHERE mat.estudiante_id = ? AND mat.materia_id = ? LIMIT 1
            """, (self.estudiante_id, materia["id"]))
            if cur.fetchone() is None:
                return f"No tengo calificaciones por componente de {materia['nombre']} sincronizadas todavía."
            return f"No pude identificar componentes con peso conocido para {materia['nombre']}."

        texto = f"Rendimiento en {materia['nombre']}:\n" + "\n".join(detalle)
        texto += f"\n\nTotal: {total_obtenido:.1f} / {total_posible:.0f} puntos evaluados hasta ahora."
        return texto

    # --- Rendimiento de TODAS las materias actuales, calculado con los componentes de Moodle ---
    def rendimiento_actual(self):
        """Nota ponderada (N1-N4, EXP1-EXP2, examen) de cada materia en curso, calculada con lo que
        ya sincronizó Moodle. A diferencia de record_academico/notas_por_semestre (que usan nota_final
        del ERP), esto no depende de que el ERP ya haya cerrado y cargado la nota: Moodle suele tener
        los componentes evaluados antes que el ERP publique la nota final."""
        cur = self.conn.execute("""
            SELECT mat.materia_id AS materia_id, m.nombre AS materia
            FROM matricula mat JOIN materia m ON m.id = mat.materia_id
            WHERE mat.estudiante_id = ? AND mat.estado = 'en_curso'
            ORDER BY m.nombre
        """, (self.estudiante_id,))
        materias = cur.fetchall()
        if not materias:
            return "No tienes materias en curso sincronizadas todavía. Usa el botón de sincronizar (⟳)."

        lineas = []
        sin_componentes = []
        for m in materias:
            detalle, obtenido, posible = self._componentes_de(m["materia_id"])
            if not detalle:
                sin_componentes.append(m["materia"])
                continue
            lineas.append(f"- {m['materia']}: {obtenido:.1f}/{posible:.0f} pts evaluados hasta ahora")
        texto = "Tu rendimiento actual según Moodle (N1-N4, EXP1-EXP2, examen; no el ERP, que suele tardar más en publicar la nota final):\n"
        texto += "\n".join(lineas) if lineas else "(ninguna materia con componentes de Moodle sincronizados aún)"
        if sin_componentes:
            texto += "\n\nSin componentes de Moodle sincronizados todavía: " + ", ".join(sin_componentes)
        return texto

    # --- Prioridad de estudio entre las materias en curso ---
    def prioridad_estudio(self):
        """EXCEPCIÓN EXPLÍCITA al principio de "no juzgar" de este proyecto (ver el docstring de
        core/seguimiento.py: ahí solo la asistencia tiene una excepción, respaldada por un reglamento
        oficial de la UNEMI). Esta sí es una recomendación -una prioridad de estudio entre materias-
        pero NO está respaldada por ningún reglamento: el usuario la pidió explícitamente y confirmó
        el criterio (tareas atrasadas y promedio bajo de Moodle, con el MISMO peso para ambas), así
        que queda documentado aquí como una decisión del usuario, no una suposición del sistema.

        Método, cálculo y formato de datos compartidos con core/seguimiento.py (que muestra lo mismo
        como un dato más del panel, no como alerta) en core/notas_moodle.py::prioridad_estudio_combinada(),
        para que no existan dos formas distintas de calcular esto en la app."""
        r = notas_moodle.prioridad_estudio_combinada(self.conn, self.estudiante_id)
        if not r["hay_materias"]:
            return "No tienes materias en curso sincronizadas todavía. Usa el botón de sincronizar (⟳)."

        if not r["ranking"]:
            texto = ("No tengo promedio de Moodle sincronizado para ninguna de tus materias en curso "
                     "todavía, así que no puedo calcular una prioridad de estudio (necesito ese dato "
                     "y el de tareas atrasadas, con el mismo peso).")
            if r.get("con_atrasadas_sin_promedio"):
                texto += "\n\nLo único que sí tengo: " + ", ".join(
                    f"{d['materia']} ({_plural_tareas(d['atrasadas'])})" for d in r["con_atrasadas_sin_promedio"])
            return texto

        lineas = [f"{i + 1}. {d['materia']} — {_plural_tareas(d['atrasadas'])}, "
                  f"promedio {d['promedio']:.0f}% en Moodle" for i, d in enumerate(r["ranking"])]
        texto = ("Prioridad de estudio entre tus materias en curso (combina, con el mismo peso, tus "
                 "tareas vencidas sin entregar y tu promedio en Moodle, a tu pedido; es un orden "
                 "sugerido con estos dos datos, no un pronóstico de tu nota final):\n"
                 + "\n".join(lineas))
        if r["sin_promedio"]:
            texto += ("\n\nSin promedio de Moodle sincronizado, no se pudieron incluir en el orden: "
                      + ", ".join(r["sin_promedio"]))
        return texto

    # --- Materias de un semestre especifico de la malla ---
    def materias_por_semestre(self, semestre: int):
        cur = self.conn.execute("""
            SELECT DISTINCT m.nombre AS materia, m.creditos
            FROM materia m
            JOIN matricula mat ON mat.materia_id = m.id AND mat.estudiante_id = ?
            WHERE m.semestre = ?
            ORDER BY m.nombre
        """, (self.estudiante_id, semestre))
        rows = cur.fetchall()
        if not rows:
            return (f"No encontré materias del semestre {semestre} en tu malla. "
                    "Usa el botón de sincronizar (⟳) o verifica el número de semestre.")
        lineas = [f"- {r['materia']} ({r['creditos']} créditos)" for r in rows]
        return f"Materias del semestre {semestre} ({len(rows)}):\n" + "\n".join(lineas)

    # --- Notas de un semestre de la malla (cruza malla + historial) ---
    def notas_por_semestre(self, semestre: int):
        """
        Materias de un nivel/semestre de la malla (ej. 1 = primer semestre)
        junto con la nota, el estado y el periodo en que las cursó el
        estudiante. Si una materia se cursó mas de una vez, salen todos los
        intentos.
        """
        cur = self.conn.execute("""
            SELECT m.nombre AS materia, mat.periodo, mat.estado, mat.nota_final, mat.asistencia
            FROM materia m
            JOIN matricula mat ON mat.materia_id = m.id AND mat.estudiante_id = ?
            WHERE m.semestre = ?
            ORDER BY m.nombre, mat.periodo
        """, (self.estudiante_id, semestre))
        rows = cur.fetchall()
        if not rows:
            return (f"No encontré materias del semestre {semestre} en tu malla/historial. "
                    "Usa el botón de sincronizar (⟳) o verifica el número de semestre.")
        lineas = []
        for r in rows:
            nota = r["nota_final"] if r["nota_final"] is not None else "sin nota"
            periodo = f" - {r['periodo']}" if r["periodo"] else ""
            lineas.append(f"- {r['materia']}: {nota} ({(r['estado'] or 'sin estado')}{periodo})")
        return f"Materias del semestre {semestre} con tu nota:\n" + "\n".join(lineas)

    # --- Materias faltantes (malla curricular vs. registro academico) ---
    def materias_faltantes(self):
        cur = self.conn.execute("""
            SELECT DISTINCT m.nombre AS materia, m.semestre, m.creditos
            FROM materia m
            JOIN matricula mat ON mat.materia_id = m.id AND mat.estudiante_id = ?
            WHERE m.id NOT IN (
                SELECT materia_id FROM matricula
                WHERE estudiante_id = ? AND estado = 'aprobada'
            )
            ORDER BY m.semestre, m.nombre
        """, (self.estudiante_id, self.estudiante_id))
        rows = cur.fetchall()
        if not rows:
            return ("No tengo tu malla curricular sincronizada todavía, o ya aprobaste "
                    "todas las materias registradas. Usa el botón de sincronizar (⟳).")
        lineas = [f"- {r['materia']} (Semestre {r['semestre']}, {r['creditos']} créditos)" for r in rows]
        return f"Materias pendientes por aprobar ({len(rows)}):\n" + "\n".join(lineas)

    # --- Lista de materias actuales ---
    def materias_actuales(self):
        """Materias en curso AHORA MISMO (estado = 'en_curso'), no el historial completo. Antes esta
        consulta no filtraba por estado y devolvía también materias de semestres ya cursados; por eso
        el chat terminaba preguntando "¿en qué semestre estás?" en vez de poder contestar directo."""
        cur = self.conn.execute("""
            SELECT m.nombre AS materia, mat.nota_final, mat.asistencia
            FROM matricula mat
            JOIN materia m ON m.id = mat.materia_id
            WHERE mat.estudiante_id = ? AND mat.estado = 'en_curso'
            ORDER BY m.nombre
        """, (self.estudiante_id,))
        rows = cur.fetchall()
        if not rows:
            return "No tienes materias en curso sincronizadas todavía. Usa el botón de sincronizar (⟳) en la parte superior de la página."
        lineas = []
        for r in rows:
            nota_txt = f" (nota: {r['nota_final']})" if r["nota_final"] is not None else ""
            lineas.append(f"- {r['materia']}{nota_txt}")
        return "Tus materias este semestre son:\n" + "\n".join(lineas)

    def close(self):
        # La conexion pertenece al hilo y se reutiliza: no se cierra aqui.
        pass


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    engine = QueryEngine()
    print("=== Motor de consultas - modo prueba ===")
    print("Escribe tu pregunta (o 'salir' para terminar)\n")

    while True:
        pregunta = input("Tú: ")
        if pregunta.lower() in ("salir", "exit", "quit"):
            break
        print(f"Asistente: {engine.responder(pregunta)}\n")

    engine.close()

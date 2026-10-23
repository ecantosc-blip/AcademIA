"""Datos y clientes simulados compartidos por las pruebas (nada de esto es real)."""
import json
import base64
from datetime import datetime, timedelta
from types import SimpleNamespace as NS


def jwt(exp: int) -> str:
    """JWT de juguete, solo con la marca de vencimiento (no es un token real)."""
    b = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return b({"alg": "HS256"}) + "." + b({"exp": exp}) + ".x"


def sembrar_demo(db_path: str) -> None:
    """Un estudiante ficticio (id 77) con materias, notas, tareas, eventos y horario."""
    from db.setup import get_connection
    c = get_connection(db_path)
    c.execute("insert into estudiante(id,nombre) values (77,'ESTUDIANTE DE PRUEBA')")
    mats = [(1, "SISTEMA DE AUTOMATIZACIÓN INDUSTRIAL", 8), (2, "DISEÑO DE INTEGRACIÓN CURRICULAR", 9),
            (3, "CÁLCULO DIFERENCIAL", 1), (4, "FÍSICA I", 1), (5, "PROGRAMACIÓN I", 2)]
    for i, n, s in mats:
        c.execute("insert into materia(id,nombre,semestre,moodle_course_id) values (?,?,?,?)", (i, n, s, 2000 + i))
    c.execute("insert into matricula(estudiante_id,materia_id,estado,nota_final,asistencia) values (77,1,'en_curso',66.7,100)")
    c.execute("insert into matricula(estudiante_id,materia_id,estado,nota_final,asistencia) values (77,2,'en_curso',27.7,95)")
    c.execute("insert into matricula(estudiante_id,materia_id,periodo,estado,nota_final) values (77,3,'ABRIL - AGOSTO 2023','aprobada',82)")
    c.execute("insert into matricula(estudiante_id,materia_id,periodo,estado,nota_final) values (77,4,'ABRIL - AGOSTO 2023','aprobada',76)")
    c.execute("insert into matricula(estudiante_id,materia_id,periodo,estado,nota_final) values (77,5,'NOVIEMBRE 2023 - MARZO 2024','pendiente',NULL)")
    for it, cal in (("Total N1", "10,00"), ("Total N2", "6,67"), ("Total N3", "-"),
                    ("Total del curso", "6,67"), ("N2 - S5-TAREA_1", "10,00")):
        c.execute("insert into componente_nota(matricula_id,nombre_item,calificacion) values (1,?,?)", (it, cal))
    f = lambda d, h: (datetime.now() + timedelta(days=d, hours=h)).isoformat(timespec="seconds")
    c.execute("insert into tarea(materia_id,moodle_assign_id,nombre,fecha_entrega) values (1,5,'S7-COMPONENTE PRÁCTICO_1',?)", (f(2, 3),))
    c.execute("insert into tarea(materia_id,moodle_assign_id,nombre,fecha_entrega) values (2,6,'Entrega de avance de tesis',?)", (f(5, 0),))
    c.execute("insert into evento(estudiante_id,nombre,tipo,fecha) values (77,'Examen teórico','examen',?)", (f(9, 0),))
    for d, i, fi, m, a in (("Lunes", "18:00", "20:00", 1, "A-101"), ("Martes", "18:00", "19:00", 2, "B-204"),
                           ("Jueves", "19:00", "21:00", 1, "A-101"), ("Viernes", "18:00", "20:00", 2, "LAB-3")):
        c.execute("insert into horario(materia_id,dia,hora_inicio,hora_fin,aula) values (?,?,?,?,?)", (m, d, i, fi, a))
    now = datetime.now()
    for k, (p, e, d) in enumerate((("moodle", "exitosa", "2 cursos"), ("sga", "exitosa", "2 materias actualizadas"),
                                   ("sga", "fallida", "Token expirado")), 0):
        c.execute("insert into sesion_extraccion(estudiante_id,plataforma,fecha,estado,detalle) values (77,?,?,?,?)",
                  (p, (now - timedelta(minutes=30 - k * 10)).isoformat(timespec="seconds"), e, d))
    c.commit()
    c.close()


class _Msgs:
    def __init__(self, padre):
        self.p = padre

    def create(self, **kw):
        self.p.llamadas.append(kw)
        herr = kw.get("tools") or []
        if herr and herr[0]["name"] == "crear_practica":
            qs = [{"subtema": f"Concepto {i+1}", "pregunta": f"Pregunta de práctica {i+1}: ¿cuál afirmación es correcta?",
                   "opciones": ["Opción uno", "Opción dos", "Opción tres", "Opción cuatro"], "correcta": 0,
                   "explicacion": f"La uno es correcta porque aplica el concepto {i+1}."} for i in range(8)]
            qs.append({"subtema": "x", "pregunta": "mala", "opciones": ["a", "a", "b", "c"], "correcta": 1, "explicacion": ""})
            return NS(content=[NS(type="tool_use", name="crear_practica", input={"preguntas": qs})])
        if herr and herr[0]["name"] == "sugerir_busquedas":
            return NS(content=[NS(type="tool_use", name="sugerir_busquedas", input={"busquedas": [
                {"busqueda": "derivadas explicadas paso a paso", "enfoque": "Introducción con ejemplos"},
                {"busqueda": "examen resuelto de derivadas", "enfoque": "NO debe pasar"},
                {"busqueda": "respuestas test cálculo", "enfoque": "NO debe pasar"},
                {"busqueda": "Derivadas explicadas paso a paso", "enfoque": "duplicada"},
                {"busqueda": "regla de la cadena clase", "enfoque": "Visualización"}]})])
        return NS(content=[NS(type="text", text="Un **concepto** se entiende mejor con un ejemplo distinto al de tu evaluación.")])


class ClienteIAFalso:
    """Sustituye al cliente de Anthropic: nunca sale a la red."""
    def __init__(self):
        self.llamadas = []
        self.messages = _Msgs(self)

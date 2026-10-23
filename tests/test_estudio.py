"""Espacio de estudio: filtro anti-respuestas, practica con modelo simulado, tutor y rutas."""
import pytest

from ayudas import ClienteIAFalso
from core.estudio import Estudio, es_pedido_de_respuestas, limpiar_busquedas, MENSAJE_RECHAZO
from db.setup import get_connection

ACTIVAS = ["Entrega de avance de tesis"]
BLOQUEA = ["dame las respuestas del test de cálculo", "resuelve mi tarea de física", "cuál es la respuesta del examen",
           "Pregunta 3: ¿Qué es un límite?\na) Un valor\nb) Una recta\nc) Un punto\nd) Nada",
           "dime la letra correcta del quiz", "hazme la tarea", "respuestas de la prueba por favor",
           "solución del cuestionario de moodle", "responde el examen de hoy", "resuelve Entrega de avance de tesis"]
PERMITE = ["explícame qué es una derivada", "¿cómo funciona la regla de la cadena?",
           "no entiendo los límites, dame un ejemplo", "resumen del tema de normalización",
           "¿qué diferencia hay entre un array y una lista?", "cómo estudio para un examen de física",
           "ayúdame a repasar integrales"]


@pytest.mark.parametrize("texto", BLOQUEA)
def test_bloquea_pedidos_de_respuestas(texto):
    assert es_pedido_de_respuestas(texto, ACTIVAS)


@pytest.mark.parametrize("texto", PERMITE)
def test_permite_dudas_legitimas(texto):
    assert not es_pedido_de_respuestas(texto, ACTIVAS)


def test_videos_filtrados_y_con_url_de_busqueda():
    v = limpiar_busquedas([{"busqueda": "derivadas explicadas", "enfoque": "x"}, {"busqueda": "examen resuelto", "enfoque": ""},
                           {"busqueda": "respuestas del test", "enfoque": ""}, {"busqueda": "Derivadas Explicadas", "enfoque": ""},
                           {"busqueda": "ab", "enfoque": ""}, "basura", {"busqueda": "regla de la cadena"}])
    assert [x["busqueda"] for x in v] == ["derivadas explicadas", "regla de la cadena"]
    assert all(x["url"].startswith("https://www.youtube.com/results?search_query=") for x in v)


def test_flujo_de_practica(bd_demo):
    fc = ClienteIAFalso()
    e = Estudio(77, client=fc)
    assert [m["nombre"] for m in e.materias()] == ["DISEÑO DE INTEGRACIÓN CURRICULAR", "SISTEMA DE AUTOMATIZACIÓN INDUSTRIAL"]
    with pytest.raises(ValueError, match="cursas"):
        e.iniciar_practica(3, "", 5)
    with pytest.raises(ValueError) as x:
        e.iniciar_practica(2, "dame las respuestas del examen", 5)
    assert str(x.value) == MENSAJE_RECHAZO
    assert len(fc.llamadas) == 0
    p = e.iniciar_practica(2, "  derivadas  ", 99, "cualquiera")
    assert p["total"] == 8
    assert not any(set(q) - {"pregunta", "opciones"} for q in p["preguntas"]), "no debe viajar la respuesta correcta"
    assert "Entrega de avance de tesis" in fc.llamadas[-1]["messages"][0]["content"]
    assert len({q["correcta"] for q in e.practica["preguntas"]}) > 1, "opciones sin mezclar"
    for i in range(8):
        q = e.practica["preguntas"][i]
        r = e.responder(i, q["correcta"] if i < 6 else (q["correcta"] + 1) % 4)
        assert r["acierto"] == (i < 6) and r["correcta"] == q["correcta"]
    assert r["terminada"] and r["resumen"]["aciertos"] == 6 and r["resumen"]["total"] == 8
    assert len(r["resumen"]["repasar"]) == 2
    with pytest.raises(ValueError, match="Ya respondiste"):
        e.responder(0, 1)
    c = get_connection()
    filas = c.execute("select * from practica").fetchall()
    c.close()
    assert len(filas) == 1 and filas[0]["aciertos"] == 6 and filas[0]["total"] == 8 and filas[0]["materia_id"] == 2
    assert [m for m in e.materias() if m["id"] == 2][0]["porcentaje"] == 75


def test_tutor(bd_demo):
    fc = ClienteIAFalso()
    e = Estudio(77, client=fc)
    antes = len(fc.llamadas)
    r = e.tutor(2, "dime la respuesta del test de hoy")
    assert r["rechazado"] and len(fc.llamadas) == antes, "debe rechazar sin llamar al modelo"
    r = e.tutor(2, "¿qué es una derivada?")
    assert not r["rechazado"] and "concepto" in r["respuesta"]
    sis = fc.llamadas[-1]["system"]
    assert "Entrega de avance de tesis" in sis and "NO PUEDES" in sis and "DISEÑO DE INTEGRACIÓN" in sis
    e.tutor(2, "otro ejemplo")
    assert [m["role"] for m in fc.llamadas[-1]["messages"]] == ["user", "assistant", "user"]
    for i in range(8):
        e.tutor(2, f"duda {i}")
    h = fc.llamadas[-1]["messages"]
    assert len(h) <= 11 and h[0]["role"] == "user"
    e.tutor(1, "hola")
    assert len(fc.llamadas[-1]["messages"]) == 1, "cambiar de materia reinicia la charla"
    assert len(e.videos(2, "derivadas")) == 2


def test_rutas_del_estudio(app_web, monkeypatch):
    A = app_web
    monkeypatch.setattr(A, "get_session", lambda: ("sid1", {"estudiante_id": 77, "nombre": "X"}))
    A.ESTUDIOS["sid1"] = Estudio(77, client=ClienteIAFalso())
    cl = A.app.test_client()
    assert cl.get("/estudio").status_code == 200
    assert len(cl.get("/api/estudio/materias").get_json()["materias"]) == 2
    r = cl.post("/api/estudio/practica", json={"materia_id": 1, "n": 5})
    assert r.status_code == 200 and r.get_json()["total"] == 5
    r = cl.post("/api/estudio/responder", json={"indice": 0, "opcion": 1})
    assert r.status_code == 200 and "correcta" in r.get_json()
    r = cl.post("/api/estudio/responder", json={"indice": 0, "opcion": 1})
    assert r.status_code == 400 and r.content_type == "application/json"
    assert cl.post("/api/estudio/tutor", json={"materia_id": 1, "mensaje": "resuelve mi tarea"}).get_json()["rechazado"]
    assert len(cl.post("/api/estudio/videos", json={"materia_id": 1}).get_json()["videos"]) >= 1
    assert cl.post("/api/estudio/tutor/limpiar").get_json()["ok"]
    monkeypatch.setattr(A, "get_session", lambda: (None, None))
    for ruta, m in (("/api/estudio/materias", "get"), ("/api/estudio/practica", "post"), ("/api/estudio/tutor", "post")):
        assert getattr(cl, m)(ruta).status_code == 401, ruta
    assert cl.get("/estudio").status_code == 302

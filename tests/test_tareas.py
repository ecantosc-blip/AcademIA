"""
Criterio unico de tareas (core/tareas.py): pendientes, vencidas, entregadas y
'sin verificar'. Chat, panel, espacio de estudio y Mis datos deben coincidir.
"""
from datetime import datetime, timezone

import pytest

from core import tiempo
from core.tareas import clasificar_tareas, en_ventana
from core.query_engine import QueryEngine
from core.estudio import Estudio
from db.setup import get_connection

AHORA = "2026-10-25T10:00:00"      # 15:00 UTC = 10:00 en Ecuador


@pytest.fixture
def datos(bd_vacia, monkeypatch):
    monkeypatch.setattr(tiempo, "_reloj", lambda: datetime(2026, 10, 25, 15, 0, 0, tzinfo=timezone.utc))
    c = get_connection()
    c.executescript("""
        INSERT INTO estudiante(id,nombre) VALUES (77,'Ana'),(78,'Beto');
        INSERT INTO materia(id,nombre,moodle_course_id) VALUES (1,'CÁLCULO',1),(2,'FÍSICA',2),(3,'AJENA',3);
        INSERT INTO matricula(estudiante_id,materia_id,estado) VALUES (77,1,'en_curso'),(77,2,'en_curso'),(78,1,'en_curso');
        INSERT INTO tarea(id,materia_id,moodle_assign_id,nombre,fecha_entrega) VALUES
          (1,1,11,'Pendiente futura','2026-10-28T10:00:00'),
          (2,1,12,'Borrador futura','2026-10-27T10:00:00'),
          (3,1,13,'Vencida sin entregar','2026-10-20T10:00:00'),
          (4,1,14,'Entregada','2026-10-26T10:00:00'),
          (5,2,15,'Sin verificar futura','2026-10-26T12:00:00'),
          (6,2,16,'Sin verificar pasada','2026-09-01T12:00:00'),
          (7,2,17,'Sin fecha sin verificar',NULL),
          (8,2,18,'Sin fecha pendiente',NULL),
          (9,3,19,'De una materia que no cursa','2026-10-28T10:00:00');
        INSERT INTO entrega(tarea_id,estudiante_id,estado) VALUES
          (1,77,'sin_entregar'),(2,77,'borrador'),(3,77,'sin_entregar'),(4,77,'entregada'),(8,77,'sin_entregar'),
          (1,78,'entregada');
    """)
    c.commit()
    yield c
    c.close()


def nombres(lista):
    return [t["tarea"] for t in lista]


# ------------------------------ clasificacion -------------------------------
def test_clasificacion_por_estado_y_fecha(datos):
    g = clasificar_tareas(datos, 77, ahora=AHORA)
    assert nombres(g["pendientes"]) == ["Borrador futura", "Pendiente futura", "Sin fecha pendiente"]
    assert nombres(g["vencidas_sin_entregar"]) == ["Vencida sin entregar"]
    assert nombres(g["entregadas"]) == ["Entregada"]
    assert nombres(g["sin_verificar"]) == ["Sin verificar futura", "Sin fecha sin verificar", "Sin verificar pasada"]
    assert "De una materia que no cursa" not in str(g)


def test_sin_verificar_nunca_es_pendiente_ni_entregada(datos):
    g = clasificar_tareas(datos, 77, ahora=AHORA)
    sv = set(nombres(g["sin_verificar"]))
    assert not sv & set(nombres(g["pendientes"])) and not sv & set(nombres(g["entregadas"]))
    assert not sv & set(nombres(g["vencidas_sin_entregar"]))


def test_los_estados_no_se_mezclan_entre_estudiantes(datos):
    g78 = clasificar_tareas(datos, 78, ahora=AHORA)
    assert nombres(g78["entregadas"]) == ["Pendiente futura"]
    assert "Pendiente futura" not in nombres(clasificar_tareas(datos, 77, ahora=AHORA)["entregadas"])
    assert nombres(g78["sin_verificar"]) == ["Entregada", "Borrador futura", "Vencida sin entregar"], \
        "sin fila de entrega para 78: todo lo suyo es 'sin verificar', aunque 77 tenga estados"
    assert all(t["materia_id"] == 1 for lista in g78.values() for t in lista)


def test_filtro_por_materia(datos):
    g = clasificar_tareas(datos, 77, materia_id=2, ahora=AHORA)
    assert all(t["materia_id"] == 2 for lista in g.values() for t in lista)


def test_limite_exacto_de_vencimiento(datos):
    datos.execute("update tarea set fecha_entrega='2026-10-25T10:00:00' where id=1")
    g = clasificar_tareas(datos, 77, ahora="2026-10-25T10:00:00")
    assert "Pendiente futura" in nombres(g["pendientes"]), "vence justo ahora: aun no esta vencida"
    g = clasificar_tareas(datos, 77, ahora="2026-10-25T10:00:01")
    assert "Pendiente futura" in nombres(g["vencidas_sin_entregar"])


def test_en_ventana(datos):
    g = clasificar_tareas(datos, 77, ahora=AHORA)
    assert nombres(en_ventana(g["pendientes"], AHORA, "2026-10-28T00:00:00")) == ["Borrador futura"]


# --------------------------------- chat --------------------------------------
def test_chat_separa_los_grupos_y_no_confunde_sin_verificar(datos):
    pend, venc, sv = QueryEngine(77).tareas_pendientes().split("\n\n")
    assert pend.startswith("Tus tareas pendientes (Moodle confirma que no las has entregado)")
    assert "Pendiente futura" in pend and "Borrador futura" in pend and "Sin fecha pendiente" in pend
    assert "la guardaste como borrador" in pend and "Sin verificar" not in pend and "Vencida" not in pend
    assert venc.startswith("Vencidas sin entregar") and "Vencida sin entregar" in venc and "Pendiente" not in venc
    assert sv.startswith("SIN VERIFICAR")
    assert "Sin verificar futura" in sv and "Sin fecha sin verificar" in sv
    assert "1 tarea(s) con fecha ya pasada" in sv and "Sin verificar pasada" not in sv
    assert "revísalas en Moodle" in sv and "ni como entregadas" in sv


def test_chat_no_lista_tareas_entregadas_ni_ajenas(datos):
    texto = QueryEngine(77).tareas_pendientes()
    assert "- Entregada " not in texto and "AJENA" not in texto and "no cursa" not in texto


def test_chat_si_todo_esta_sin_verificar_lo_dice_y_no_dice_que_no_hay_pendientes(datos):
    datos.execute("delete from entrega")
    datos.commit()
    texto = QueryEngine(77).tareas_pendientes()
    assert texto.startswith("SIN VERIFICAR") and "Moodle no me informó" in texto
    assert "Moodle confirma" not in texto, "no debe haber secciones de pendientes ni vencidas"
    assert "No tienes tareas pendientes" not in texto
    assert "- Pendiente futura" in texto, "se lista, pero bajo SIN VERIFICAR"


def test_chat_cuando_no_hay_nada_pendiente(datos):
    datos.execute("delete from entrega")
    datos.execute("delete from tarea")
    datos.commit()
    assert QueryEngine(77).tareas_pendientes() == "No tienes tareas pendientes registradas."


def test_chat_solo_entregadas_dice_que_no_hay_pendientes(datos):
    datos.execute("delete from entrega where estudiante_id=77")
    datos.execute("insert into entrega(tarea_id,estudiante_id,estado) select id,77,'entregada' from tarea")
    datos.commit()
    assert "No tienes tareas pendientes" in QueryEngine(77).tareas_pendientes()


def test_chat_por_materia(datos):
    t = QueryEngine(77).tareas_por_materia("calculo")
    assert "Pendiente futura" in t and "Sin verificar futura" not in t and "FÍSICA" not in t
    t = QueryEngine(77).tareas_por_materia("fisica")
    assert "SIN VERIFICAR" in t and "Sin verificar futura" in t and "Sin fecha pendiente" in t


# ------------------- coherencia entre chat, panel, estudio y Mis datos -------
def test_chat_y_panel_coinciden(datos):
    from panel import construir_panel
    p = construir_panel({"estudiante_id": 77, "nombre": "X"})
    g = clasificar_tareas(datos, 77)
    esperado = {t["tarea"] for t in g["pendientes"] + [x for x in g["sin_verificar"] if not x["vencida"]] if t["fecha"]}
    en_panel = {f["nombre"] for f in p["fechas"] if f["tipo"] == "Tarea"}
    assert en_panel == esperado
    estados = {f["nombre"]: f["estado"] for f in p["fechas"] if f["tipo"] == "Tarea"}
    assert estados["Sin verificar futura"] == "sin_verificar" and estados["Pendiente futura"] == "sin_entregar"
    assert estados["Borrador futura"] == "borrador"
    texto = QueryEngine(77).tareas_pendientes()
    for nombre in en_panel:
        assert nombre in texto
    assert "Sin verificar pasada" not in en_panel and "Vencida sin entregar" not in en_panel


def test_kpis_del_inicio_no_cuentan_sin_verificar_como_pendiente(datos):
    from panel import construir_panel
    i = construir_panel({"estudiante_id": 77, "nombre": "X"})["inicio"]
    # hasta 7 dias: 'Borrador futura' (27) y 'Pendiente futura' (28) son confirmadas; 'Sin verificar futura' no
    assert i["vencen_7d"] == 2 and i["sin_verificar_7d"] == 1 and i["vencidas_sin_entregar"] == 1


def test_el_espacio_de_estudio_protege_tambien_lo_sin_verificar(datos):
    """Para el filtro anti-trampas, una evaluacion con estado desconocido cuenta como activa."""
    from ayudas import ClienteIAFalso
    datos.execute("update materia set nombre='FÍSICA' where id=2")
    datos.execute("insert into tarea(id,materia_id,moodle_assign_id,nombre,fecha_entrega) values (20,2,20,'Quiz sin verificar','2026-10-26T09:00:00')")
    datos.execute("insert into entrega(tarea_id,estudiante_id,estado) values (5,77,'entregada')")
    datos.commit()
    activas = Estudio(77, client=ClienteIAFalso()).evaluaciones_activas(2)
    assert "Quiz sin verificar" in activas
    assert "Sin verificar futura" not in activas, "esta si esta confirmada como entregada"


def test_mis_datos_exporta_el_estado_real(datos):
    import mis_datos
    filas = {f["tarea"]: f for f in mis_datos.exportar(77)["tareas_y_entregas"]}
    assert filas["Entregada"]["estado_entrega"] == "entregada"
    assert filas["Sin verificar futura"]["estado_entrega"] == "sin_verificar"
    assert filas["Borrador futura"]["estado_entrega"] == "borrador"

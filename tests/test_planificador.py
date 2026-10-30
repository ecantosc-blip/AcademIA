"""Plan de estudio: reparto explicable, respeta fechas y tiempo disponible, y dice cuando algo no cabe."""
import pytest

from core import planificador as PL
from core import preferencias as P
from db.setup import get_connection

E = 77
AHORA = "2026-11-02T09:00:00"        # lunes


@pytest.fixture
def con(bd_demo):
    c = get_connection()
    c.execute("PRAGMA foreign_keys = ON")
    # escenario fijo: tarea 5 (materia 1) vence el miercoles; tarea 6 (materia 2) el viernes; examen el jueves 5
    c.execute("update tarea set fecha_entrega='2026-11-04T10:00:00' where moodle_assign_id=5")
    c.execute("update tarea set fecha_entrega='2026-11-06T10:00:00' where moodle_assign_id=6")
    c.execute("update evento set fecha='2026-11-05T09:00:00' where nombre='Examen teórico'")
    for assign in (5, 6):
        c.execute("insert into entrega(tarea_id,estudiante_id,estado,verificada_en) select id,?,'sin_entregar','x' from tarea where moodle_assign_id=?", (E, assign))
    c.commit()
    yield c
    c.close()


def disp(c, **dias):
    P.guardar(c, E, {"disponibilidad": dias})


def plan(c, ahora=AHORA):
    return PL.generar(c, E, ahora)


def bloques(p):
    return [(d["dia"], b["item"], b["minutos"]) for d in p["dias"] for b in d["bloques"]]


def test_sin_disponibilidad_no_inventa_horas(con):
    p = plan(con)
    assert p["configurado"] is False and bloques(p) == []
    assert p["total_disponible_min"] == 0
    assert {x["item"] for x in p["por_planificar"]} == {"S7-COMPONENTE PRÁCTICO_1", "Entrega de avance de tesis", "Examen teórico"}
    assert all(x["minutos"] > 0 for x in p["por_planificar"])


def test_reparte_primero_lo_mas_urgente(con):
    disp(con, lunes=120, martes=120)
    p = plan(con)
    por_item = {}
    for _, item, m in bloques(p):
        por_item[item] = por_item.get(item, 0) + m
    assert por_item == {"S7-COMPONENTE PRÁCTICO_1": 60, "Entrega de avance de tesis": 60, "Examen teórico": 120}
    lunes = p["dias"][0]["bloques"]
    assert lunes[0]["item"] == "S7-COMPONENTE PRÁCTICO_1"          # vence antes: va primero
    assert p["no_alcanza"] == [] and p["sin_espacio"] == []


def test_nunca_pasa_del_tiempo_disponible_ni_de_la_fecha_limite(con):
    disp(con, lunes=50, martes=40, miercoles=200, jueves=200, viernes=200)
    p = plan(con)
    for d in p["dias"]:
        assert d["usado_min"] <= d["disponible_min"]
        assert sum(b["minutos"] for b in d["bloques"]) == d["usado_min"]
    limites = {"S7-COMPONENTE PRÁCTICO_1": "2026-11-04", "Entrega de avance de tesis": "2026-11-06",
               "Examen teórico": "2026-11-04"}                    # un examen se estudia antes de su dia
    for d in p["dias"]:
        for b in d["bloques"]:
            assert d["fecha"] <= limites[b["item"]], (d["fecha"], b)


def test_la_dificultad_cambia_los_minutos(con):
    disp(con, lunes=300)
    base = {i: m for _, i, m in bloques(plan(con))}
    P.guardar_dificultad(con, E, 1, 3)
    P.guardar_dificultad(con, E, 2, 1)
    dif = {i: m for _, i, m in bloques(plan(con))}
    assert base["S7-COMPONENTE PRÁCTICO_1"] == 60 and dif["S7-COMPONENTE PRÁCTICO_1"] == 90      # dificil x1.5
    assert base["Entrega de avance de tesis"] == 60 and dif["Entrega de avance de tesis"] == 45  # facil x0.75


def test_si_no_cabe_antes_de_vencer_lo_dice_con_los_minutos_que_faltan(con):
    disp(con, lunes=30, martes=15)                                # 45 min en total; la tarea 5 necesita 60 y vence el miercoles
    p = plan(con)
    n = next(x for x in p["no_alcanza"] if x["item"] == "S7-COMPONENTE PRÁCTICO_1")
    assert (n["total_min"], n["faltan_min"]) == (60, 15)
    assert [(d, m) for d, i, m in bloques(p) if i == "S7-COMPONENTE PRÁCTICO_1"] == [("lunes", 30), ("martes", 15)]
    # lo demas tampoco cabe, y se dice en lugar de esconderlo
    assert {x["item"]: x["faltan_min"] for x in p["no_alcanza"]} == {
        "S7-COMPONENTE PRÁCTICO_1": 15, "Examen teórico": 120, "Entrega de avance de tesis": 60}


def test_tiempo_solo_despues_de_la_fecha_limite_no_sirve(con):
    disp(con, jueves=300, viernes=300)                            # la tarea 5 vence el miercoles
    p = plan(con)
    assert not [b for b in bloques(p) if b[1] == "S7-COMPONENTE PRÁCTICO_1"]
    assert any(x["item"] == "S7-COMPONENTE PRÁCTICO_1" and x["faltan_min"] == 60 for x in p["no_alcanza"])


def test_lo_vencido_sin_entregar_va_primero_y_dice_por_que(con):
    con.execute("insert into tarea(materia_id,moodle_assign_id,nombre,fecha_entrega) values (2,70,'Informe atrasado','2026-10-28T10:00:00')")
    con.execute("insert into entrega(tarea_id,estudiante_id,estado,verificada_en) select id,?,'borrador','x' from tarea where moodle_assign_id=70", (E,))
    con.commit()
    disp(con, lunes=60)
    p = plan(con)
    assert p["dias"][0]["bloques"][0]["item"] == "Informe atrasado"
    assert "Ya venció" in p["dias"][0]["bloques"][0]["motivo"]


def test_las_tareas_sin_verificar_y_las_entregadas_no_se_planifican(con):
    con.execute("update entrega set estado='entregada' where tarea_id=(select id from tarea where moodle_assign_id=6)")
    con.execute("delete from entrega where tarea_id=(select id from tarea where moodle_assign_id=5)")
    con.commit()
    disp(con, lunes=300)
    p = plan(con)
    assert {b[1] for b in bloques(p)} == {"Examen teórico"}        # ni la entregada ni la sin verificar
    assert [x["item"] for x in p["sin_verificar"]] == ["S7-COMPONENTE PRÁCTICO_1"]


def test_solo_los_eventos_que_parecen_evaluaciones_entran(con):
    con.execute("insert into evento(estudiante_id,nombre,tipo,fecha) values (?,'Taller de socialización','otro','2026-11-04T09:00:00')", (E,))
    con.commit()
    disp(con, lunes=300)
    assert "Taller de socialización" not in {b[1] for b in bloques(plan(con))}


def test_quitar_del_plan_libera_tiempo_y_se_puede_volver_a_incluir(con):
    disp(con, lunes=60)
    clave = f"t:{con.execute('select id from tarea where moodle_assign_id=5').fetchone()[0]}"
    assert [b[1] for b in bloques(plan(con))] == ["S7-COMPONENTE PRÁCTICO_1"]
    P.excluir(con, E, clave)
    p = plan(con)
    assert [x["item"] for x in p["quitados"]] == ["S7-COMPONENTE PRÁCTICO_1"]
    assert "S7-COMPONENTE PRÁCTICO_1" not in {b[1] for b in bloques(p)}
    P.incluir(con, E, clave)
    assert plan(con)["quitados"] == []


def test_lo_que_vence_despues_del_plan_se_lista_aparte(con):
    con.execute("update tarea set fecha_entrega='2026-12-20T10:00:00' where moodle_assign_id=6")
    con.commit()
    disp(con, lunes=300)
    p = plan(con)
    assert [x["item"] for x in p["fuera_del_plan"]] == ["Entrega de avance de tesis"]
    assert "Entrega de avance de tesis" not in {b[1] for b in bloques(p)}


def test_tarea_sin_fecha_usa_solo_lo_que_sobra(con):
    con.execute("update tarea set fecha_entrega=NULL where moodle_assign_id=6")
    con.commit()
    disp(con, lunes=240)
    p = plan(con)
    assert sum(m for _, i, m in bloques(p) if i == "Entrega de avance de tesis") == 60
    disp(con, lunes=120)                                          # ya no sobra tiempo
    p = plan(con)
    assert [x["item"] for x in p["sin_espacio"]] == ["Entrega de avance de tesis"]
    assert all(x["item"] != "Entrega de avance de tesis" for x in p["no_alcanza"])


def test_cada_bloque_explica_su_motivo_y_las_suposiciones_son_visibles(con):
    disp(con, lunes=300)
    p = plan(con)
    for d in p["dias"]:
        for b in d["bloques"]:
            assert "Estimado" in b["motivo"] and "min base" in b["motivo"]
    assert any("dificultad media (no la indicaste)" in b["motivo"] for d in p["dias"] for b in d["bloques"])
    assert len(p["supuestos"]) == 3 and "no mediciones" in p["supuestos"][0]


def test_es_determinista(con):
    disp(con, lunes=90, martes=60, miercoles=120)
    assert plan(con) == plan(con)


def test_cada_estudiante_planifica_solo_lo_suyo(con):
    con.execute("insert into estudiante(id,nombre) values (78,'OTRA')")
    P.guardar(con, 78, {"disponibilidad": {"lunes": 300}})
    p = PL.generar(con, 78, AHORA)
    assert bloques(p) == [] and p["por_planificar"] == []


def test_una_semana_completa_siempre_trae_siete_dias(con):
    p = plan(con)
    assert [d["dia"] for d in p["dias"]] == ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

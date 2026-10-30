"""Alertas: configurables, sin duplicados, siempre al dia con la situacion real."""
import pytest

from core import alertas as A
from core import preferencias as P
from db.setup import get_connection

E = 77
AHORA = "2026-11-02T09:00:00"


@pytest.fixture
def con(bd_demo):
    c = get_connection()
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("update tarea set fecha_entrega='2026-11-03T20:00:00' where moodle_assign_id=5")   # vence en 35 h
    c.execute("update tarea set fecha_entrega='2026-11-10T20:00:00' where moodle_assign_id=6")   # lejos
    c.execute("delete from sesion_extraccion")
    for plataforma in ("moodle", "sga"):
        c.execute("insert into sesion_extraccion(estudiante_id,plataforma,fecha,estado,detalle) values (?,?,?,?,?)",
                  (E, plataforma, "2026-11-02T08:00:00", "exitosa", "ok"))
    for assign in (5, 6):
        c.execute("insert into entrega(tarea_id,estudiante_id,estado,verificada_en) select id,?,'sin_entregar','x' from tarea where moodle_assign_id=?", (E, assign))
    c.commit()
    yield c
    c.close()


def tipos(c):
    return sorted(a["tipo"] for a in A.activas(c, E))


def test_avisa_de_la_entrega_que_vence_pronto_y_no_de_la_lejana(con):
    assert A.generar(con, E, AHORA) == 1
    (a,) = A.activas(con, E)
    assert a["tipo"] == "vence_pronto" and "S7-COMPONENTE PRÁCTICO_1" in a["mensaje"] and "3 nov, 20:00" in a["mensaje"]


def test_no_se_duplica_por_mucho_que_se_genere(con):
    assert A.generar(con, E, AHORA) == 1
    for _ in range(5):
        assert A.generar(con, E, AHORA) == 0
    assert len(A.activas(con, E)) == 1
    assert con.execute("select count(*) from alerta").fetchone()[0] == 1


def test_una_alerta_descartada_no_vuelve(con):
    A.generar(con, E, AHORA)
    assert A.marcar_vistas(con, E) == 1
    assert A.generar(con, E, AHORA) == 0 and A.activas(con, E) == []
    assert A.generar(con, E, "2026-11-02T15:00:00") == 0          # ni horas despues


def test_si_cambia_la_fecha_es_otra_alerta(con):
    A.generar(con, E, AHORA)
    A.marcar_vistas(con, E)
    con.execute("update tarea set fecha_entrega='2026-11-03T22:00:00' where moodle_assign_id=5")
    con.commit()
    assert A.generar(con, E, AHORA) == 1
    assert "3 nov, 22:00" in A.activas(con, E)[0]["mensaje"]


def test_se_retira_sola_si_la_causa_desaparece(con):
    A.generar(con, E, AHORA)
    con.execute("update entrega set estado='entregada' where tarea_id=(select id from tarea where moodle_assign_id=5)")
    con.commit()
    A.generar(con, E, AHORA)
    assert A.activas(con, E) == []


def test_la_anticipacion_es_configurable(con):
    P.guardar(con, E, {"alerta_horas": 24})
    assert A.generar(con, E, AHORA) == 0                          # faltan 35 h > 24 h
    P.guardar(con, E, {"alerta_horas": 48})
    assert A.generar(con, E, AHORA) == 1
    P.guardar(con, E, {"alerta_horas": 24})
    A.generar(con, E, AHORA)
    assert A.activas(con, E) == []                                # al reducir el aviso, la alerta ya no aplica


def test_se_puede_desactivar_un_tipo(con):
    P.guardar(con, E, {"alertas_tipos": ["vencida"]})
    assert A.generar(con, E, AHORA) == 0
    P.guardar(con, E, {"alertas_tipos": []})
    assert A.generar(con, E, AHORA) == 0


def test_vencida_sin_entregar_y_limite_de_30_dias(con):
    con.execute("update tarea set fecha_entrega='2026-10-30T10:00:00' where moodle_assign_id=5")     # vencida hace 3 dias
    con.execute("update tarea set fecha_entrega='2026-08-01T10:00:00' where moodle_assign_id=6")     # vencida hace meses
    con.commit()
    A.generar(con, E, AHORA)
    assert tipos(con) == ["vencida"]
    assert "venció el 30 oct" in A.activas(con, E)[0]["mensaje"]


def test_las_tareas_sin_verificar_nunca_generan_alerta_de_entrega(con):
    con.execute("delete from entrega")
    con.commit()
    assert A.generar(con, E, AHORA) == 0


def test_lectura_fallida_una_alerta_por_racha(con):
    def fallo(fecha):
        con.execute("insert into sesion_extraccion(estudiante_id,plataforma,fecha,estado,detalle) values (?,?,?,?,?)",
                    (E, "moodle", fecha, "fallida", "x"))
        con.commit()
    fallo("2026-11-02T08:30:00")
    con.execute("update entrega set estado='entregada'")              # aislar: solo se mira la lectura
    con.commit()
    A.generar(con, E, AHORA)
    assert tipos(con) == ["lectura_fallida"] and "de Moodle" in A.activas(con, E)[0]["mensaje"]
    fallo("2026-11-02T08:40:00")                                      # otro fallo en la misma racha
    assert A.generar(con, E, AHORA) == 0
    A.marcar_vistas(con, E)
    fallo("2026-11-02T08:50:00")
    assert A.generar(con, E, AHORA) == 0                              # sigue siendo la misma racha
    con.execute("insert into sesion_extraccion(estudiante_id,plataforma,fecha,estado,detalle) values (?,?,?,?,?)",
                (E, "moodle", "2026-11-02T08:55:00", "exitosa", "ok"))
    con.commit()
    A.generar(con, E, AHORA)
    fallo("2026-11-02T08:58:00")                                      # racha nueva despues de una lectura buena
    assert A.generar(con, E, AHORA) == 1


def test_asistencia_solo_avisa_si_la_persona_puso_un_minimo(con):
    A.generar(con, E, AHORA)
    assert "asistencia_baja" not in tipos(con)                        # sin minimo configurado no se inventa uno
    P.guardar(con, E, {"asistencia_minima": 96})
    A.generar(con, E, AHORA)
    msgs = [a["mensaje"] for a in A.activas(con, E) if a["tipo"] == "asistencia_baja"]
    assert len(msgs) == 1 and "95%" in msgs[0] and "mínimo que indicaste (96%)" in msgs[0]
    assert A.generar(con, E, AHORA) == 0
    P.guardar(con, E, {"asistencia_minima": 90})
    A.generar(con, E, AHORA)
    assert "asistencia_baja" not in tipos(con)


def test_marcar_algunas_y_no_las_de_otros(con):
    con.execute("update tarea set fecha_entrega='2026-11-03T20:00:00' where moodle_assign_id=6")
    con.commit()
    A.generar(con, E, AHORA)
    uno, otro = [a["id"] for a in A.activas(con, E)]
    con.execute("insert into estudiante(id,nombre) values (78,'OTRA')")
    assert A.marcar_vistas(con, 78, [uno]) == 0                       # no puede descartar alertas ajenas
    assert A.marcar_vistas(con, E, [uno]) == 1
    assert [a["id"] for a in A.activas(con, E)] == [otro]
    assert A.marcar_vistas(con, E, []) == 0


def test_cada_estudiante_ve_solo_las_suyas(con):
    con.execute("insert into estudiante(id,nombre) values (78,'OTRA')")
    con.commit()
    A.generar(con, E, AHORA)
    assert A.generar(con, 78, AHORA) == 0 and A.activas(con, 78) == []


def test_las_vistas_muy_antiguas_se_limpian(con):
    A.generar(con, E, AHORA)
    A.marcar_vistas(con, E, ahora="2026-08-01T10:00:00")
    A.generar(con, E, AHORA)
    assert con.execute("select count(*) from alerta where vista_en is not null").fetchone()[0] == 0

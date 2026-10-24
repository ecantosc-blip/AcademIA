"""Detector de cambios: se inyectan cambios concretos entre dos fotos y se revisa que se detecten
exactamente esos (y nada mas)."""
import pytest

from core import cambios
from db.setup import get_connection

E = 77
VENCE = "2026-10-30T23:59:00"


@pytest.fixture
def con(bd_demo):
    c = get_connection()
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()


def _tipos(c, plataforma="moodle"):
    return sorted(r["tipo"] for r in c.execute(
        "select tipo from cambio where estudiante_id=? and plataforma=?", (E, plataforma)))


def _partida(con, plataforma="moodle"):
    assert cambios.registrar(con, E, plataforma) == 0
    con.commit()


def test_la_primera_vez_solo_guarda_la_foto_de_partida(con):
    assert cambios.registrar(con, E, "moodle") == 0
    con.commit()
    assert _tipos(con) == []
    assert con.execute("select count(*) from foto_estado where estudiante_id=?", (E,)).fetchone()[0] == 1


def test_sin_cambios_no_genera_nada_ni_duplicados(con):
    _partida(con)
    assert cambios.registrar(con, E, "moodle") == 0
    assert cambios.registrar(con, E, "moodle") == 0
    assert _tipos(con) == []


def test_tarea_nueva(con):
    _partida(con)
    con.execute("insert into tarea(materia_id,moodle_assign_id,nombre,fecha_entrega) values (1,99,'Tarea recién publicada',?)", (VENCE,))
    assert cambios.registrar(con, E, "moodle") == 1
    c = con.execute("select * from cambio").fetchone()
    assert (c["tipo"], c["item"], c["despues"], c["materia_id"]) == ("tarea_nueva", "Tarea recién publicada", VENCE, 1)


def test_fecha_de_entrega_movida(con):
    _partida(con)
    antes = con.execute("select fecha_entrega from tarea where moodle_assign_id=5").fetchone()[0]
    con.execute("update tarea set fecha_entrega=? where moodle_assign_id=5", (VENCE,))
    assert cambios.registrar(con, E, "moodle") == 1
    c = con.execute("select * from cambio").fetchone()
    assert (c["tipo"], c["antes"], c["despues"]) == ("tarea_fecha", antes, VENCE)


def test_estado_de_entrega_cambia_solo_si_ambos_estados_son_conocidos(con):
    tid = con.execute("select id from tarea where moodle_assign_id=5").fetchone()[0]
    _partida(con)                                           # tarea sin estado (sin verificar)
    con.execute("insert into entrega(tarea_id,estudiante_id,estado,verificada_en) values (?,?,'sin_entregar','x')", (tid, E))
    assert cambios.registrar(con, E, "moodle") == 0         # pasar de "sin verificar" a conocido NO es un cambio
    con.execute("update entrega set estado='entregada' where tarea_id=?", (tid,))
    assert cambios.registrar(con, E, "moodle") == 1
    c = con.execute("select * from cambio").fetchone()
    assert (c["tipo"], c["antes"], c["despues"]) == ("tarea_estado", "sin_entregar", "entregada")


def test_notas_nueva_cambiada_e_irrelevantes(con):
    _partida(con)
    con.execute("update componente_nota set calificacion='8,50' where nombre_item='Total N3'")     # "-" -> nota
    con.execute("update componente_nota set calificacion='7,00' where nombre_item='Total N2'")     # 6,67 -> 7,00
    con.execute("update componente_nota set calificacion='10' where nombre_item='Total N1'")       # "10,00" -> "10": igual
    con.execute("insert into componente_nota(matricula_id,nombre_item,calificacion) values (1,'N4 - Tarea sin calificar','-')")
    con.execute("insert into componente_nota(matricula_id,nombre_item,calificacion) values (1,'N4 - Tarea calificada','9,00')")
    assert cambios.registrar(con, E, "moodle") == 3
    por_item = {r["item"]: (r["tipo"], r["antes"], r["despues"]) for r in con.execute("select * from cambio")}
    assert por_item == {"Total N3": ("nota_nueva", "-", "8,50"),
                        "Total N2": ("nota_cambio", "6,67", "7,00"),
                        "N4 - Tarea calificada": ("nota_nueva", None, "9,00")}


def test_eventos_nuevo_fecha_y_desaparicion(con):
    _partida(con)
    con.execute("insert into evento(estudiante_id,nombre,tipo,fecha) values (?,'Exposición final','otro',?)", (E, VENCE))
    con.execute("update evento set fecha='2026-12-01T08:00:00' where nombre='Examen teórico'")
    assert cambios.registrar(con, E, "moodle") == 2
    assert _tipos(con) == ["evento_fecha", "evento_nuevo"]
    con.execute("delete from evento")                        # desaparecen solos con el tiempo: no es un cambio
    assert cambios.registrar(con, E, "moodle") == 0


def test_sga_nota_final_y_asistencia(con):
    _partida(con, "sga")
    con.execute("update matricula set nota_final=70.5, asistencia=90 where estudiante_id=? and materia_id=1", (E,))
    con.execute("update matricula set nota_final=NULL where estudiante_id=? and materia_id=2", (E,))   # dato retirado
    assert cambios.registrar(con, E, "sga") == 2
    assert _tipos(con, "sga") == ["sga_asistencia", "sga_nota_final"]
    c = con.execute("select * from cambio where tipo='sga_nota_final'").fetchone()
    assert (float(c["antes"]), float(c["despues"]), c["materia_id"]) == (66.7, 70.5, 1)


def test_un_cambio_se_registra_una_sola_vez(con):
    _partida(con)
    con.execute("update tarea set fecha_entrega=? where moodle_assign_id=5", (VENCE,))
    for _ in range(3):
        cambios.registrar(con, E, "moodle")
    assert con.execute("select count(*) from cambio").fetchone()[0] == 1


def test_foto_ilegible_o_de_otro_formato_se_toma_como_partida(con):
    _partida(con)
    for basura in ("no es json", '{"v": 999}', "[]"):
        con.execute("update foto_estado set datos=?", (basura,))
        con.execute("update tarea set fecha_entrega=? where moodle_assign_id=5", (basura[:3] + "x",))
        assert cambios.registrar(con, E, "moodle") == 0
    assert con.execute("select count(*) from cambio").fetchone()[0] == 0


def test_cada_plataforma_y_cada_estudiante_tienen_su_propia_foto(con):
    con.execute("insert into estudiante(id,nombre) values (78,'OTRA PERSONA')")
    _partida(con, "moodle")
    cambios.registrar(con, 78, "moodle")
    con.execute("update tarea set fecha_entrega=? where moodle_assign_id=5", (VENCE,))
    assert cambios.registrar(con, 78, "moodle") == 0      # 78 no cursa esa materia: no ve nada
    assert cambios.registrar(con, E, "moodle") == 1
    assert cambios.contar_nuevas(con, 78) == 0


def test_novedades_textos_y_marcar_como_vistas(con):
    _partida(con)
    con.execute("insert into tarea(materia_id,moodle_assign_id,nombre,fecha_entrega) values (1,99,'Informe 3',?)", (VENCE,))
    con.execute("update componente_nota set calificacion='8,50' where nombre_item='Total N3'")
    cambios.registrar(con, E, "moodle")
    con.commit()
    nov = cambios.novedades(con, E)
    textos = {n["texto"] for n in nov}
    assert "Nueva tarea: Informe 3 (vence 30 oct, 23:59)" in textos
    assert "Nota publicada en Total N3: 8,5" in textos
    assert cambios.contar_nuevas(con, E) == 2
    assert cambios.marcar_vistas(con, E) == 2
    assert cambios.contar_nuevas(con, E) == 0
    assert cambios.novedades(con, E) == []
    assert len(cambios.novedades(con, E, solo_nuevas=False)) == 2
    assert cambios.marcar_vistas(con, E) == 0

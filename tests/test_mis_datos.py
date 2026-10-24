"""Panel 'Mis datos': resumen, exportacion sin secretos y borrado en cascada."""
import json

import mis_datos
from db.setup import get_connection


def test_resumen_y_exportacion_sin_secretos(bd_demo):
    r = mis_datos.resumen(77)
    assert r["conteos"] and "ultima_sincronizacion" in r
    ex = mis_datos.exportar(77)
    assert ex["estudiante"]["id"] == 77 and ex["matriculas"]
    texto = json.dumps(ex, ensure_ascii=False)
    assert "eyJ" not in texto and "sga_access_token" not in texto


def test_borrar_elimina_todo_lo_del_estudiante_y_conserva_el_catalogo(bd_demo):
    c = get_connection()
    materias_antes = c.execute("select count(*) from materia").fetchone()[0]
    c.close()
    mis_datos.borrar(77)
    c = get_connection()
    for q in ("select count(*) from estudiante where id=77",
              "select count(*) from matricula where estudiante_id=77",
              "select count(*) from componente_nota",
              "select count(*) from evento where estudiante_id=77",
              "select count(*) from sesion_extraccion where estudiante_id=77"):
        assert c.execute(q).fetchone()[0] == 0, q
    assert c.execute("select count(*) from materia").fetchone()[0] == materias_antes
    c.close()


def test_novedades_se_ven_se_exportan_y_se_borran(bd_demo):
    from core import cambios
    from db.setup import get_connection
    c = get_connection()
    c.execute("PRAGMA foreign_keys = ON")
    cambios.registrar(c, 77, "moodle")
    c.execute("update tarea set fecha_entrega='2026-12-01T10:00:00' where moodle_assign_id=5")
    cambios.registrar(c, 77, "moodle")
    c.commit()
    c.close()
    assert {x["nombre"]: x["cantidad"] for x in mis_datos.resumen(77)["conteos"]}["Novedades detectadas"] == 1
    exp = mis_datos.exportar(77)
    assert [n["tipo"] for n in exp["novedades"]] == ["tarea_fecha"]
    mis_datos.borrar(77)
    c = get_connection()
    assert c.execute("select count(*) from cambio").fetchone()[0] == 0
    assert c.execute("select count(*) from foto_estado").fetchone()[0] == 0
    c.close()

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

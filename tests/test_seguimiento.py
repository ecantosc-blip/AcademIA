"""Indicador de seguimiento: reglas simples sobre datos verificados, sin predicciones."""
import pytest

from core import cambios, seguimiento
from db.setup import get_connection

E = 77
AHORA = "2026-10-30T12:00:00"


@pytest.fixture
def con(bd_demo):
    c = get_connection()
    c.execute("PRAGMA foreign_keys = ON")
    # escenario controlado: fechas fijas y lecturas recientes y buenas
    c.execute("update tarea set fecha_entrega='2026-11-20T10:00:00' where moodle_assign_id=5")
    c.execute("update tarea set fecha_entrega='2026-11-25T10:00:00' where moodle_assign_id=6")
    c.execute("delete from sesion_extraccion")
    for plataforma in ("moodle", "sga"):
        c.execute("insert into sesion_extraccion(estudiante_id,plataforma,fecha,estado,detalle) values (?,?,?,?,?)",
                  (E, plataforma, "2026-10-30T09:00:00", "exitosa", "ok"))
    c.commit()
    yield c
    c.close()


def _tid(c, assign):
    return c.execute("select id from tarea where moodle_assign_id=?", (assign,)).fetchone()[0]


def _estado(c, assign, estado):
    c.execute("insert or replace into entrega(tarea_id,estudiante_id,estado,verificada_en) values (?,?,?,?)",
              (_tid(c, assign), E, estado, AHORA))
    c.commit()


def _lecturas_recientes(c, ahora):
    """Deja las lecturas de Moodle y ERP como hechas una hora antes de `ahora` (para aislar otras reglas)."""
    from datetime import datetime, timedelta
    f = (datetime.fromisoformat(ahora) - timedelta(hours=1)).isoformat(timespec="seconds")
    c.execute("update sesion_extraccion set fecha=?", (f,))
    c.commit()


def _regla(res, id_):
    return next(r for r in res["reglas"] if r["id"] == id_)


def test_todo_verificado_y_al_dia_no_muestra_alertas(con):
    _estado(con, 5, "entregada")
    _estado(con, 6, "entregada")
    res = seguimiento.evaluar(con, E, AHORA)
    assert res["resumen"]["nivel"] == "ok" and res["resumen"]["atencion"] == 0
    assert [r["estado"] for r in res["reglas"]] == ["ok", "ok", "ok", "info", "ok", "ok"]


def test_tareas_sin_verificar_no_son_pendientes_ni_entregadas(con):
    res = seguimiento.evaluar(con, E, AHORA)               # ninguna tarea tiene estado en este escenario
    sv = _regla(res, "sin_verificar")
    assert sv["estado"] == "sin_datos" and len(sv["items"]) == 2
    assert _regla(res, "vencidas")["estado"] == "ok" and _regla(res, "proximas")["estado"] == "ok"
    assert res["resumen"]["nivel"] == "sin_datos"
    assert "no pude verificar" in res["resumen"]["texto"]


def test_vencida_sin_entregar_pide_atencion_y_se_explica(con):
    _estado(con, 5, "sin_entregar")
    _lecturas_recientes(con, "2026-11-21T08:00:00")
    res = seguimiento.evaluar(con, E, "2026-11-21T08:00:00")          # un dia despues del vencimiento
    r = _regla(res, "vencidas")
    assert r["estado"] == "atencion" and "1 tarea vencida" in r["detalle"]
    assert r["items"][0]["texto"].startswith("S7-COMPONENTE PRÁCTICO_1 · ")
    assert "sin entregar" in r["porque"] and "30" in r["porque"]
    assert res["resumen"]["nivel"] == "atencion" and res["resumen"]["texto"] == "1 punto de atención."


def test_una_tarea_entregada_vencida_no_cuenta(con):
    _estado(con, 5, "entregada")
    assert _regla(seguimiento.evaluar(con, E, "2026-11-21T08:00:00"), "vencidas")["estado"] == "ok"


def test_vence_pronto_solo_dentro_de_48_horas(con):
    _estado(con, 5, "borrador")                                      # vence el 20/11 a las 10:00
    esperado = {"2026-11-19T10:00:00": "atencion",                   # faltan 24 h
                "2026-11-18T10:00:00": "atencion",                   # faltan exactamente 48 h
                "2026-11-18T09:59:00": "ok",                         # faltan 48 h y 1 min
                "2026-11-18T09:00:00": "ok"}
    for ahora, estado in esperado.items():
        assert _regla(seguimiento.evaluar(con, E, ahora), "proximas")["estado"] == estado, ahora
    r = _regla(seguimiento.evaluar(con, E, "2026-11-19T10:00:00"), "proximas")
    assert "vence" in r["detalle"] and "48 horas" in r["detalle"]


def test_sin_verificar_que_vence_pronto_no_se_cuenta_como_pendiente(con):
    res = seguimiento.evaluar(con, E, "2026-11-19T10:00:00")          # la tarea 5 vence en 24 h, sin estado
    assert _regla(res, "proximas")["estado"] == "ok"
    assert _regla(res, "sin_verificar")["estado"] == "sin_datos"


def test_asistencia_por_encima_del_minimo_oficial_es_solo_info(con):
    """100% y 95% (semilla de bd_demo): ninguna materia baja del 60% del reglamento, asi que es
    solo un dato, no una alerta."""
    r = _regla(seguimiento.evaluar(con, E, AHORA), "asistencia")
    assert r["estado"] == "info" and r["detalle"] == "Tu asistencia más baja es 95% en DISEÑO DE INTEGRACIÓN CURRICULAR (por encima del 60% mínimo)."
    con.execute("update matricula set asistencia=NULL where estudiante_id=?", (E,))
    con.commit()
    assert _regla(seguimiento.evaluar(con, E, AHORA), "asistencia")["estado"] == "sin_datos"


def test_asistencia_bajo_60_es_atencion_citando_el_reglamento(con):
    """Unico umbral oficial del panel: menos de 60% (Reglamento de Facultades de Grado/Reglamento de
    Facultades de la UNEMI) -> pierde examen final/recuperacion y reprueba por inasistencia."""
    con.execute("update matricula set asistencia=10 where estudiante_id=? and materia_id=1", (E,))
    con.commit()
    r = _regla(seguimiento.evaluar(con, E, AHORA), "asistencia")
    assert r["estado"] == "atencion"
    assert "1 materia está bajo el 60% de asistencia" in r["detalle"]
    assert "reprobarías por inasistencia" in r["detalle"]
    assert r["items"] == [{"texto": "SISTEMA DE AUTOMATIZACIÓN INDUSTRIAL · 10% de asistencia", "fecha": None}]
    assert "Reglamento de Facultades" in r["porque"]


def test_asistencia_bajo_60_en_varias_materias_las_lista_todas(con):
    con.execute("update matricula set asistencia=10 where estudiante_id=? and materia_id=1", (E,))
    con.execute("update matricula set asistencia=59 where estudiante_id=? and materia_id=2", (E,))
    con.commit()
    r = _regla(seguimiento.evaluar(con, E, AHORA), "asistencia")
    assert r["estado"] == "atencion" and "2 materias están bajo el 60%" in r["detalle"]
    nombres = {i["texto"].split(" · ")[0] for i in r["items"]}
    assert nombres == {"SISTEMA DE AUTOMATIZACIÓN INDUSTRIAL", "DISEÑO DE INTEGRACIÓN CURRICULAR"}


def test_novedades_pendientes_se_informan_sin_alarmar(con):
    cambios.registrar(con, E, "moodle")
    con.execute("update tarea set fecha_entrega='2026-12-01T10:00:00' where moodle_assign_id=5")
    cambios.registrar(con, E, "moodle")
    con.commit()
    r = _regla(seguimiento.evaluar(con, E, AHORA), "novedades")
    assert r["estado"] == "info" and r["detalle"] == "1 novedad sin ver."
    cambios.marcar_vistas(con, E)
    assert _regla(seguimiento.evaluar(con, E, AHORA), "novedades")["estado"] == "ok"


@pytest.mark.parametrize("filas,estado,texto", [
    ([], "sin_datos", "Aún no hay una lectura de Moodle."),
    ([("moodle", "2026-10-30T09:00:00", "exitosa"), ("moodle", "2026-10-30T11:00:00", "fallida"),
      ("sga", "2026-10-30T09:00:00", "exitosa")], "atencion", "La última lectura de Moodle falló"),
    ([("moodle", "2026-10-27T09:00:00", "exitosa"), ("sga", "2026-10-30T09:00:00", "exitosa")],
     "atencion", "más de 2 días"),
    ([("moodle", "2026-10-30T09:00:00", "exitosa"), ("sga", "2026-10-30T09:00:00", "exitosa")], "ok", "recientemente"),
])
def test_datos_al_dia(con, filas, estado, texto):
    con.execute("delete from sesion_extraccion")
    for plataforma, fecha, est in filas:
        con.execute("insert into sesion_extraccion(estudiante_id,plataforma,fecha,estado,detalle) values (?,?,?,?,'x')",
                    (E, plataforma, fecha, est))
    con.commit()
    r = _regla(seguimiento.evaluar(con, E, AHORA), "datos")
    assert r["estado"] == estado and texto in r["detalle"]


def test_una_lectura_fallida_que_despues_se_recupera_vuelve_a_ok(con):
    con.execute("insert into sesion_extraccion(estudiante_id,plataforma,fecha,estado,detalle) values (?,?,?,?,?)",
                (E, "moodle", "2026-10-30T11:00:00", "fallida", "x"))
    con.execute("insert into sesion_extraccion(estudiante_id,plataforma,fecha,estado,detalle) values (?,?,?,?,?)",
                (E, "moodle", "2026-10-30T11:30:00", "exitosa", "ok"))
    con.commit()
    assert _regla(seguimiento.evaluar(con, E, AHORA), "datos")["estado"] == "ok"


def test_se_limita_la_lista_de_items(con):
    for n in range(10):
        con.execute("insert into tarea(materia_id,moodle_assign_id,nombre,fecha_entrega) values (1,?,?,'2026-10-01T10:00:00')",
                    (100 + n, f"Tarea vieja {n}"))
        con.execute("insert into entrega(tarea_id,estudiante_id,estado,verificada_en) select id,?,'sin_entregar','x' from tarea where moodle_assign_id=?",
                    (E, 100 + n))
    con.commit()
    r = _regla(seguimiento.evaluar(con, E, AHORA), "vencidas")
    assert "10 tareas vencidas" in r["detalle"] and len(r["items"]) == seguimiento.MAX_ITEMS


def test_nunca_habla_de_prediccion_ni_de_riesgo(con):
    _estado(con, 5, "sin_entregar")
    _lecturas_recientes(con, "2026-11-21T08:00:00")
    res = seguimiento.evaluar(con, E, "2026-11-21T08:00:00")
    texto = " ".join([res["resumen"]["texto"]] + [r["titulo"] + r["detalle"] + r["porque"] for r in res["reglas"]]).lower()
    for palabra in ("riesgo", "probabilidad", "reprob", "predic", "aprobar", "aprobarás", "vas a"):
        assert palabra not in texto


def test_los_textos_gramaticales_de_las_lecturas(con):
    con.execute("delete from sesion_extraccion")
    con.commit()
    d = _regla(seguimiento.evaluar(con, E, AHORA), "datos")["detalle"]
    assert "Aún no hay una lectura de Moodle." in d and "Aún no hay una lectura del ERP." in d
    assert " de el " not in d


def test_cada_estudiante_ve_solo_lo_suyo(con):
    con.execute("insert into estudiante(id,nombre) values (78,'OTRA PERSONA')")
    con.commit()
    res = seguimiento.evaluar(con, 78, AHORA)
    assert _regla(res, "sin_verificar")["estado"] == "ok"          # 78 no cursa nada: no ve las tareas de 77
    assert _regla(res, "datos")["estado"] == "sin_datos"

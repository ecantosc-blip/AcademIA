"""Plan con horas: franjas - clases - actividades, prioridad por Moodle y refuerzo."""
from datetime import datetime

from core import agenda_personal as AP
from core import planificador
from core import preferencias as P
from core.tiempo import ahora_iso
from db.setup import get_connection

EST = 77
# Lunes 2030-01-07 08:00 (ficticio y lejano: el demo usa fechas relativas a hoy, por eso se fijan tareas propias)
AHORA = "2030-01-07T08:00:00"


def _limpiar(c):
    c.execute("DELETE FROM tarea"); c.execute("DELETE FROM evento"); c.commit()


def _tareas(c, filas):
    _limpiar(c)
    for i, (mid, nombre, fecha) in enumerate(filas, 100):
        c.execute("INSERT INTO tarea(materia_id, moodle_assign_id, nombre, fecha_entrega) VALUES (?,?,?,?)", (mid, i, nombre, fecha))
        c.execute("INSERT INTO entrega(tarea_id, estudiante_id, estado, verificada_en) "
                  "SELECT id, 77, 'sin_entregar', 'x' FROM tarea WHERE moodle_assign_id=?", (i,))
    c.commit()


def _pct(c, materia, valor):
    mat = c.execute("SELECT id FROM matricula WHERE estudiante_id=? AND materia_id=?", (EST, materia)).fetchone()[0]
    c.execute("DELETE FROM componente_nota WHERE matricula_id=?", (mat,))
    c.execute("INSERT INTO componente_nota(matricula_id,nombre_item,calificacion,porcentaje) VALUES (?,?,?,?)",
              (mat, "Total del curso", "1", f"{valor},00 %"))
    c.commit()


def test_el_plan_usa_las_horas_libres_y_no_las_clases(app_web):
    c = get_connection()
    try:
        _tareas(c, [(1, "Tarea A", "2030-01-08T23:00:00")])
        # lunes: libre 16-22, pero hay clase 18-20 (demo) y una actividad 21-22
        AP.guardar_franjas(c, EST, [{"dia": "lunes", "inicio": "16:00", "fin": "22:00"}])
        AP.guardar_actividades(c, EST, [{"dia": "lunes", "inicio": "21:00", "fin": "22:00", "nombre": "Cena"}])
        p = planificador.generar(c, EST, ahora=AHORA)
    finally:
        c.close()
    assert p["con_horas"] and p["configurado"]
    lunes = p["dias"][0]
    horas = [(b["inicio"], b["fin"]) for b in lunes["bloques"]]
    assert horas == [("16:00", "17:00")] or horas[0] == ("16:00", "17:00")      # 60 min por tarea, antes de la clase
    assert all(not ("18:00" <= (b["inicio"] or "") < "20:00") for b in lunes["bloques"])
    assert all(b["recomendacion"] for b in lunes["bloques"])


def test_hoy_no_se_planifica_en_el_pasado(app_web):
    c = get_connection()
    try:
        _tareas(c, [(1, "Tarea A", "2030-01-09T23:00:00")])
        AP.guardar_franjas(c, EST, [{"dia": "lunes", "inicio": "07:00", "fin": "10:00"}])
        p = planificador.generar(c, EST, ahora="2030-01-07T08:32:00")
    finally:
        c.close()
    primero = p["dias"][0]["bloques"][0]
    assert primero["inicio"] == "08:35" and primero["fin"] == "09:35"


def test_la_materia_con_menor_porcentaje_se_adelanta_solo_unos_dias(app_web):
    c = get_connection()
    try:
        # misma ventana de entrega aproximada: la de la materia 2 vence unas horas DESPUES que la de la 1
        _tareas(c, [(1, "Tarea de la 1", "2030-01-10T10:00:00"), (2, "Tarea de la 2", "2030-01-10T20:00:00")])
        AP.guardar_franjas(c, EST, [{"dia": "lunes", "inicio": "08:00", "fin": "09:00"}])
        _pct(c, 1, 90); _pct(c, 2, 40)
        con = planificador.generar(c, EST, ahora=AHORA)
        _pct(c, 2, 90)   # iguales: no se prioriza
        sin = planificador.generar(c, EST, ahora=AHORA)
    finally:
        c.close()
    assert con["dias"][0]["bloques"][0]["item"] == "Tarea de la 2" and "menor porcentaje" in con["dias"][0]["bloques"][0]["motivo"]
    assert con["prioridad_moodle"][0]["puesto"] == 1 and con["prioridad_moodle"][0]["porcentaje"] == 40.0
    assert sin["dias"][0]["bloques"][0]["item"] == "Tarea de la 1" and sin["prioridad_moodle"] == []


def test_lo_vencido_sigue_primero_aunque_haya_prioridad(app_web):
    c = get_connection()
    try:
        _tareas(c, [(1, "Vencida de la 1", "2030-01-05T10:00:00"), (2, "Tarea de la 2", "2030-01-08T10:00:00")])
        AP.guardar_franjas(c, EST, [{"dia": "lunes", "inicio": "08:00", "fin": "09:00"}])
        _pct(c, 1, 90); _pct(c, 2, 30)
        p = planificador.generar(c, EST, ahora=AHORA)
    finally:
        c.close()
    assert p["dias"][0]["bloques"][0]["item"] == "Vencida de la 1"


def test_refuerzo_solo_con_tiempo_que_sobra_y_en_las_materias_mas_bajas(app_web):
    c = get_connection()
    try:
        _tareas(c, [(1, "Tarea A", "2030-01-08T23:00:00")])
        AP.guardar_franjas(c, EST, [{"dia": "lunes", "inicio": "08:00", "fin": "10:00"},
                                    {"dia": "miercoles", "inicio": "08:00", "fin": "08:20"}])
        _pct(c, 1, 90); _pct(c, 2, 40)
        p = planificador.generar(c, EST, ahora=AHORA)
    finally:
        c.close()
    lunes = p["dias"][0]["bloques"]
    ref = [b for b in lunes if b["tipo"] == "refuerzo"]
    assert len(ref) == 1 and ref[0]["minutos"] <= 45 and ref[0]["clave"].startswith("r:")
    assert "40 %" in ref[0]["motivo"]
    assert [b for b in p["dias"][2]["bloques"] if b["tipo"] == "refuerzo"] == []      # 20 min: no cabe un refuerzo


def test_sin_franjas_el_plan_sigue_igual_y_sin_horas(app_web):
    c = get_connection()
    try:
        p = planificador.generar(c, EST, ahora=AHORA)
    finally:
        c.close()
    assert p["con_horas"] is False and p["configurado"] is False


def test_el_tope_diario_nunca_hace_perder_una_fecha_limite(app_web):
    """El margen del 70% no se respeta a costa de una entrega: si una tarea de 150 min vence HOY y
    solo hay 180 min libres hoy (70% serian apenas 126, no alcanzarian), se usa el tiempo libre
    completo que haga falta en vez de dejarla en "no alcanza" por dejar margen."""
    c = get_connection()
    try:
        P.guardar(c, EST, {"minutos_por_tarea": 150})
        _tareas(c, [(1, "Tarea urgente", "2030-01-07T23:00:00")])     # vence hoy mismo
        AP.guardar_franjas(c, EST, [{"dia": "lunes", "inicio": "08:00", "fin": "11:00"}])  # 3h; 70% = 2h06 (126 min)
        p = planificador.generar(c, EST, ahora=AHORA)
    finally:
        c.close()
    lunes = [b for b in p["dias"][0]["bloques"] if b["clave"].startswith("t:")]
    assert sum(b["minutos"] for b in lunes) == 150     # supera el 70% (126 min): el limite de hoy manda
    assert not p["no_alcanza"]


def test_no_llena_un_dia_solo_porque_cabe_reparte_al_siguiente(app_web):
    """Con 6 horas libres en un solo dia y 6 tareas de 1 hora, el plan no mete las 6 ese mismo dia
    nada mas porque "caben": deja un margen (30% del dia) y reparte lo que sobra al dia siguiente,
    aunque ninguna tenga la fecha encima. Si una fecha limite lo exigiera, si lo llenaria completo
    (eso lo cubren los demas tests de esta clase: el tope nunca hace perder una entrega)."""
    c = get_connection()
    try:
        _tareas(c, [(1, f"Tarea {i}", "2030-01-13T23:00:00") for i in range(1, 7)])
        AP.guardar_franjas(c, EST, [{"dia": d, "inicio": "08:00", "fin": "14:00"}
                                    for d in ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")])
        p = planificador.generar(c, EST, ahora=AHORA)
    finally:
        c.close()
    lunes = [b for b in p["dias"][0]["bloques"] if b["clave"].startswith("t:")]
    martes = [b for b in p["dias"][1]["bloques"] if b["clave"].startswith("t:")]
    assert len(lunes) == 4, "4 tareas de 1h caben en el 70% de las 6h libres (4h12); la 5a ya no"
    assert len(martes) == 2, "las 2 que no cupieron en el margen del lunes pasan al martes, no se pierden"
    assert sum(b["minutos"] for b in lunes) <= 0.7 * 360
    assert not p["no_alcanza"] and not p["sin_espacio"]      # nada se pierde: solo se reparte

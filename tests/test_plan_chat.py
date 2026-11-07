"""Herramientas del chatbot para armar el plan conversando (sin llamar a ningun modelo)."""
from unittest import mock

from core import plan_chat, assistant
from db.setup import get_connection

EST = 77


def test_las_herramientas_estan_registradas_sin_duplicados():
    nombres = [t["name"] for t in assistant.TOOLS]
    assert len(nombres) == len(set(nombres))
    assert plan_chat.NOMBRES <= set(nombres)
    assert "confirm" in assistant.SYSTEM_PROMPT and "NUNCA supongas" in assistant.SYSTEM_PROMPT


def test_guardar_y_ver_horario_personal(app_web):
    c = get_connection()
    try:
        assert "nada guardado" in plan_chat.ejecutar("ver_horario_personal", c, EST, {})
        r = plan_chat.ejecutar("guardar_franjas_libres", c, EST,
                               {"franjas": [{"dia": "lunes", "inicio": "16:00", "fin": "18:00"}]})
        assert "1 franja" in r
        r = plan_chat.ejecutar("guardar_actividades", c, EST,
                               {"actividades": [{"dia": "lunes", "inicio": "07:00", "fin": "15:00", "nombre": "Trabajo"}]})
        assert "1 actividad" in r
        t = plan_chat.ejecutar("ver_horario_personal", c, EST, {})
        assert "lunes: libre 16:00-18:00" in t and "Trabajo" in t and "18:00-20:00" in t   # clase del ERP
    finally:
        c.close()


def test_un_error_de_validacion_vuelve_como_texto_y_no_guarda(app_web):
    c = get_connection()
    try:
        r = plan_chat.ejecutar("guardar_franjas_libres", c, EST, {"franjas": [{"dia": "lunes", "inicio": "25:00", "fin": "26:00"}]})
        assert r.startswith("No se guardó nada") and "25:00" in r
        assert c.execute("SELECT COUNT(*) FROM franja_libre WHERE estudiante_id=?", (EST,)).fetchone()[0] == 0
        assert "Se borraron" in plan_chat.ejecutar("guardar_actividades", c, EST, {"actividades": []})
    finally:
        c.close()


def test_generar_plan_sin_franjas_pide_la_disponibilidad_y_con_franjas_da_horas(app_web):
    c = get_connection()
    try:
        assert "no ha indicado franjas" in plan_chat.ejecutar("generar_plan_estudio", c, EST, {})
        c.execute("INSERT INTO entrega(tarea_id, estudiante_id, estado, verificada_en) "
                  "SELECT id, 77, 'sin_entregar', 'x' FROM tarea WHERE moodle_assign_id = 5")
        c.commit()
        plan_chat.ejecutar("guardar_franjas_libres", c, EST, {"franjas": [
            {"dia": d, "inicio": "08:00", "fin": "12:00"} for d in ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")]})
        t = plan_chat.ejecutar("generar_plan_estudio", c, EST, {})
    finally:
        c.close()
    assert "PLAN DE ESTUDIO" in t and "por qué:" in t and "cómo estudiar:" in t and "Supuestos:" in t
    assert "S7-COMPONENTE PRÁCTICO_1" in t


def test_el_asistente_despacha_las_herramientas_del_plan(app_web):
    a = assistant.AssistantLLM.__new__(assistant.AssistantLLM)
    a.acciones = []
    a.engine = mock.Mock(estudiante_id=EST)
    a.engine.conn = get_connection()
    try:
        out = a._ejecutar_herramienta("guardar_franjas_libres", {"franjas": [{"dia": "viernes", "inicio": "19:00", "fin": "21:00"}]})
        assert "1 franja" in out and a.acciones == ["horario"]       # la interfaz mostrara «Ver mi horario»
        out = a._ejecutar_herramienta("guardar_franjas_libres", {"franjas": "no es lista"})
        assert out.startswith("No se guardó nada")
    finally:
        a.engine.conn.close()


# ----------------------------------------------------------------- Plan v2: botones y bloques
def _asistente(c):
    a = assistant.AssistantLLM.__new__(assistant.AssistantLLM)
    a.acciones, a.opciones = [], None
    a.engine = mock.Mock(estudiante_id=EST)
    a.engine.conn = c
    return a


def test_hacer_pregunta_prepara_botones_y_valida(app_web):
    a = _asistente(get_connection())
    out = a._ejecutar_herramienta("hacer_pregunta", {"pregunta": "¿Cuándo prefieres estudiar?",
                                  "opciones": ["Mañana", "Tarde", "Noche"], "varias": False})
    assert out.startswith("Listo") and a.opciones == {"pregunta": "¿Cuándo prefieres estudiar?", "opciones": ["Mañana", "Tarde", "Noche"], "varias": False}
    a.opciones = None
    assert a._ejecutar_herramienta("hacer_pregunta", {"pregunta": "x", "opciones": ["solo una"]}).startswith("Error")
    assert a._ejecutar_herramienta("hacer_pregunta", {"pregunta": "", "opciones": ["a", "b"]}).startswith("Error")
    assert a._ejecutar_herramienta("hacer_pregunta", {"pregunta": "x", "opciones": [str(i) for i in range(9)]}).startswith("Error")
    assert a.opciones is None and "hacer_pregunta" not in plan_chat.NOMBRES and "hacer_pregunta" in [t["name"] for t in assistant.TOOLS]


def test_agregar_y_quitar_actividad_por_chat(app_web):
    c = get_connection()
    try:
        assert "Listo" in plan_chat.ejecutar("agregar_actividad", c, EST, {"nombre": "Almuerzo", "dias": ["Lunes", "martes"], "inicio": "13:00", "fin": "14:00"})
        assert "Listo" in plan_chat.ejecutar("agregar_actividad", c, EST, {"nombre": "Gimnasio", "dias": ["lunes"], "inicio": "17:00", "fin": "18:00"})
        assert plan_chat.ejecutar("agregar_actividad", c, EST, {"nombre": "x", "dias": [], "inicio": "1", "fin": "2"}).startswith("No se guardó")
        assert len(AP_listar(c)) == 3
        assert "Listo" in plan_chat.ejecutar("quitar_actividad", c, EST, {"nombre": "almuerzo", "dia": "lunes"})
        assert sorted((a["dia"], a["nombre"]) for a in AP_listar(c)) == [("lunes", "Gimnasio"), ("martes", "Almuerzo")]
        plan_chat.ejecutar("quitar_actividad", c, EST, {"nombre": "ALMUERZO"})
        assert plan_chat.ejecutar("quitar_actividad", c, EST, {"nombre": "inexistente"}).startswith("No encontré")
    finally:
        c.close()


def AP_listar(c):
    from core import agenda_personal
    return agenda_personal.listar(c, EST)["actividades"]


def test_mover_bloque_por_chat_pide_aclarar_si_hay_duda_y_fija_si_es_claro(app_web):
    c = get_connection()
    try:
        c.execute("DELETE FROM tarea"); c.execute("DELETE FROM evento")
        c.execute("INSERT INTO tarea(materia_id, moodle_assign_id, nombre, fecha_entrega) VALUES (1, 701, 'Informe de redes', ?)", (__import__("core.tiempo", fromlist=["x"]).ahora_iso("seconds", days=4),))
        c.execute("INSERT INTO entrega(tarea_id, estudiante_id, estado, verificada_en) SELECT id,77,'sin_entregar','x' FROM tarea")
        c.commit()
        from core import agenda_personal
        agenda_personal.guardar_franjas(c, EST, [{"dia": d, "inicio": "07:00", "fin": "24:00".replace("24", "23")} for d in
                                                 ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")])
        assert plan_chat.ejecutar("mover_bloque_estudio", c, EST, {"texto": "inexistente", "dia": "lunes", "inicio": "09:00"}) == "No encontré ese bloque en el plan."
        r = plan_chat.ejecutar("mover_bloque_estudio", c, EST, {"texto": "informe", "dia": "xx", "inicio": "09:00"})
        assert r.startswith("No se movió") and "no es un día válido" in r
        r = plan_chat.ejecutar("mover_bloque_estudio", c, EST, {"texto": "informe", "dia": "jueves", "inicio": "10:00"})
        assert "fijado" in r or "No se movió" in r            # depende de la hora real del reloj; no debe fallar con error interno
        assert plan_chat.ejecutar("soltar_bloques_fijados", c, EST, {}).startswith(("Listo", "No había"))
    finally:
        c.close()

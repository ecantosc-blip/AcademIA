"""Rutas del plan de estudio y de las alertas (sesion simulada)."""
from unittest import mock

from core import preferencias as P
from db.setup import get_connection

SESION = {"estudiante_id": 77, "nombre": "ESTUDIANTE DE PRUEBA", "sga_access_token": "x",
          "sga_refresh_token": None, "moodle_token": "m"}


def _cli(A, estudiante=77):
    p = mock.patch.object(A, "get_session", return_value=("sid1", dict(SESION, estudiante_id=estudiante)))
    p.start()
    return A.app.test_client(), p


def test_sin_sesion_todo_da_401(app_web):
    with mock.patch.object(app_web, "get_session", return_value=(None, None)):
        c = app_web.app.test_client()
        for ruta in ("/api/plan", "/api/alertas"):
            assert c.get(ruta).status_code == 401
        for ruta in ("/api/plan/preferencias", "/api/plan/excluir", "/api/plan/incluir", "/api/alertas/vistas",
                     "/api/resumen_semanal"):
            assert c.post(ruta, json={}).status_code == 401


def test_resumen_semanal_pide_para_el_estudiante_de_la_sesion_y_no_llama_a_la_ia_en_cada_carga(app_web):
    """La ruta solo debe reaccionar a un POST explicito (se pide, no se genera solo) y
    debe usar el estudiante_id de la sesion, no uno inventado."""
    from core import resumen_semanal as R
    c, p = _cli(app_web)
    try:
        with mock.patch.object(R, "generar", return_value={"completado": [], "pendiente": [],
                               "proximas_evaluaciones": [], "sugerencias": [], "mensaje": "ok", "generado": True}) as m:
            assert c.get("/api/resumen_semanal").status_code == 405, "no debe generarse solo con un GET"
            d = c.post("/api/resumen_semanal", json={}).get_json()
            assert d["mensaje"] == "ok" and m.call_args.args[1] == 77
    finally:
        p.stop()


def test_el_plan_sin_configurar_lo_dice_y_lista_las_materias(app_web):
    c, p = _cli(app_web)
    try:
        d = c.get("/api/plan").get_json()
    finally:
        p.stop()
    assert d["plan"]["configurado"] is False and d["preferencias"]["disponibilidad"] == {}
    assert [m["nombre"] for m in d["materias"]] == ["DISEÑO DE INTEGRACIÓN CURRICULAR", "SISTEMA DE AUTOMATIZACIÓN INDUSTRIAL"]
    assert all(m["dificultad"] is None for m in d["materias"]) and d["dias"][0] == "lunes"


def test_guardar_preferencias_y_dificultad_regenera_el_plan(app_web):
    c, p = _cli(app_web)
    try:
        r = c.post("/api/plan/preferencias", json={"disponibilidad": {"lunes": 120, "martes": 120, "miercoles": 120,
                                                                      "jueves": 120, "viernes": 120, "sabado": 60, "domingo": 60},
                                                   "minutos_por_tarea": 90, "dificultades": {"1": 3}})
        d = r.get_json()
    finally:
        p.stop()
    assert r.status_code == 200 and d["plan"]["configurado"] is True
    assert d["preferencias"]["minutos_por_tarea"] == 90
    assert {m["id"]: m["dificultad"] for m in d["materias"]}[1] == 3
    assert d["plan"]["total_disponible_min"] == (120 * 5 + 120) * 2   # 14 dias = 2 semanas completas


def test_un_valor_invalido_da_400_y_no_guarda_nada(app_web):
    c, p = _cli(app_web)
    try:
        r = c.post("/api/plan/preferencias", json={"minutos_por_tarea": 90, "dificultades": {"1": 3, "2": 9}})
        assert r.status_code == 400 and "entero entre 1 y 3" in r.get_json()["error"]
        for malo in ({}, {"inventada": 1}, {"dificultades": "x"}, {"dificultades": {"999": 2}}, {"alerta_horas": 5}):
            assert c.post("/api/plan/preferencias", json=malo).status_code == 400, malo
        assert c.post("/api/plan/preferencias", data="no es json", content_type="text/plain").status_code == 400
    finally:
        p.stop()
    con = get_connection()
    assert P.obtener(con, 77)["minutos_por_tarea"] == 60 and P.dificultades(con, 77) == {}
    con.close()


def test_quitar_y_volver_a_incluir(app_web):
    c, p = _cli(app_web)
    try:
        c.post("/api/plan/preferencias", json={"disponibilidad": {"lunes": 300}})
        con = get_connection()
        con.execute("insert into entrega(tarea_id,estudiante_id,estado,verificada_en) select id,77,'sin_entregar','x' from tarea where moodle_assign_id=5")
        con.commit()
        tid = con.execute("select id from tarea where moodle_assign_id=5").fetchone()[0]
        con.close()
        d = c.post("/api/plan/excluir", json={"clave": f"t:{tid}"}).get_json()
        assert [q["item"] for q in d["plan"]["quitados"]] == ["S7-COMPONENTE PRÁCTICO_1"]
        assert c.post("/api/plan/excluir", json={"clave": "mala"}).status_code == 400
        d = c.post("/api/plan/incluir", json={"clave": f"t:{tid}"}).get_json()
        assert d["plan"]["quitados"] == []
    finally:
        p.stop()


def test_alertas_se_listan_se_descartan_y_no_vuelven(app_web):
    con = get_connection()
    from core.tiempo import ahora_iso
    con.execute("update tarea set fecha_entrega=? where moodle_assign_id=5", (ahora_iso("seconds", hours=5),))
    con.execute("insert into entrega(tarea_id,estudiante_id,estado,verificada_en) select id,77,'sin_entregar','x' from tarea where moodle_assign_id=5")
    con.commit()
    con.close()
    c, p = _cli(app_web)
    try:
        a = c.get("/api/alertas").get_json()["alertas"]
        assert [x["tipo"] for x in a if x["tipo"] == "vence_pronto"] == ["vence_pronto"]
        assert c.get("/api/panel").get_json()["alertas"] == a                     # el panel muestra las mismas, sin duplicar
        assert c.post("/api/alertas/vistas", json={"ids": ["x"]}).status_code == 400
        assert c.post("/api/alertas/vistas", json={"ids": [True]}).status_code == 400
        r = c.post("/api/alertas/vistas", json={}).get_json()
        assert r["marcadas"] >= 1 and r["alertas"] == []
        assert c.get("/api/alertas").get_json()["alertas"] == []                  # descartadas: no vuelven
        assert c.get("/api/panel").get_json()["alertas"] == []
    finally:
        p.stop()


def test_no_se_pueden_descartar_alertas_de_otra_persona(app_web):
    con = get_connection()
    con.execute("insert into estudiante(id,nombre) values (78,'OTRA')")
    con.execute("insert into alerta(estudiante_id,tipo,clave,mensaje,creada_en) values (78,'vencida','k','ajena','2026-10-30T10:00:00')")
    ajena = con.execute("select id from alerta").fetchone()[0]
    con.commit()
    con.close()
    c, p = _cli(app_web, estudiante=77)
    try:
        assert c.post("/api/alertas/vistas", json={"ids": [ajena]}).get_json()["marcadas"] == 0
    finally:
        p.stop()
    con = get_connection()
    assert con.execute("select vista_en from alerta where id=?", (ajena,)).fetchone()[0] is None
    con.close()


def test_assets_de_marca_y_csp_locales():
    """El tema UNEMI sirve tigre y fuentes desde el propio sitio (sin CDN)."""
    from web.app import app
    c = app.test_client()
    assert c.get("/static/tiger-avatar.png").status_code == 200
    assert c.get("/static/fonts/poppins-latin-400-normal.woff2").status_code == 200
    csp = c.get("/login").headers.get("Content-Security-Policy", "")
    assert "font-src 'self'" in csp and "fonts.googleapis" not in csp


# ------------------------------------------------------------ foto de perfil
BASE = "https://aula.unemi.edu.ec"


def test_url_de_foto_solo_del_mismo_sitio_y_https(app_web):
    f = __import__("core.foto",fromlist=["x"]).url_valida
    ok = BASE + "/pluginfile.php/5/user/icon/f1"
    assert f(ok, BASE) == ok
    assert f("http://aula.unemi.edu.ec/pluginfile.php/5/user/icon/f1", BASE) == ""
    assert f("https://otro-sitio.com/pluginfile.php/5/user/icon/f1", BASE) == ""
    assert f(BASE + "/theme/image.php/boost/core/1/u/f1", BASE) == ""
    assert f(None, BASE) == "" and f("no es url", BASE) == ""


class _Resp:
    def __init__(self, status=200, tipo="image/jpeg", datos=b"JPG"):
        self.status_code, self.headers = status, {"Content-Type": tipo}
        self.raw = mock.Mock(read=mock.Mock(return_value=datos))


def _foto(app_web, resp, foto_url=BASE + "/pluginfile.php/5/user/icon/f1"):
    ses = dict(SESION, foto_url=foto_url)
    with mock.patch.object(app_web, "get_session", return_value=("s", ses)), \
         mock.patch.object(app_web.requests, "get", return_value=resp) as g:
        r = app_web.app.test_client().get("/api/foto")
    return r, g


def test_foto_se_reenvia_con_el_token_solo_en_el_servidor(app_web):
    r, g = _foto(app_web, _Resp())
    assert r.status_code == 200 and r.data == b"JPG" and r.mimetype == "image/jpeg"
    assert r.headers["Cache-Control"] == "no-store"
    url = g.call_args.args[0]
    assert "/webservice/pluginfile.php/" in url and g.call_args.kwargs["params"] == {"token": "m"}
    assert g.call_args.kwargs["allow_redirects"] is False


def test_foto_rechaza_lo_que_no_es_imagen_o_es_enorme(app_web):
    assert _foto(app_web, _Resp(tipo="text/html"))[0].status_code == 404
    assert _foto(app_web, _Resp(tipo="image/svg+xml"))[0].status_code == 404
    assert _foto(app_web, _Resp(status=303))[0].status_code == 404
    assert _foto(app_web, _Resp(datos=b"x" * (__import__("core.foto",fromlist=["x"]).MAX_BYTES + 1)))[0].status_code == 404
    assert _foto(app_web, _Resp(), foto_url="")[0].status_code == 404


def test_foto_sin_sesion_da_401(app_web):
    with mock.patch.object(app_web, "get_session", return_value=(None, None)):
        assert app_web.app.test_client().get("/api/foto").status_code == 401


def test_foto_prueba_la_segunda_ruta_y_no_sigue_redirecciones_ajenas():
    from core import foto
    url = BASE + "/pluginfile.php/5/user/icon/f1"
    # la ruta de webservice falla (404) y la normal funciona
    with mock.patch.object(foto.requests, "get", side_effect=[_Resp(404), _Resp()]) as g:
        datos, tipo, motivo = foto.descargar(url, "tok")
    assert datos == b"JPG" and motivo == "ok" and g.call_count == 2
    # redireccion a otro sitio: no se sigue (el token no sale de Moodle)
    r = _Resp(302); r.headers["Location"] = "https://malo.example/x.jpg"
    with mock.patch.object(foto.requests, "get", return_value=r) as g:
        datos, _, motivo = foto.descargar(url, "tok")
    assert datos is None and "otro sitio" in motivo
    assert all("malo.example" not in c.args[0] for c in g.call_args_list)


def test_foto_del_erp_se_busca_solo_en_unemi_y_manda_el_token_solo_al_erp():
    from core import foto
    base = "https://sga.unemi.edu.ec"
    jwt = {"nombre": "X", "perfilprincipal": {"id": 1, "persona": {"foto": "/media/fotos/a.jpg"}}}
    assert foto.buscar_en_sga(jwt, base) == base + "/media/fotos/a.jpg"
    assert foto.buscar_en_sga({"foto": "https://malo.com/a.jpg"}, base) == ""
    assert foto.buscar_en_sga({"foto": "https://sga.unemi.edu.ec/x.txt"}, base) == ""
    with mock.patch.object(foto.requests, "get", return_value=_Resp()) as g:
        assert foto.descargar_erp(base + "/m/a.jpg", "TOK", base)[0] == b"JPG"
        assert g.call_args.kwargs["headers"] == {"Authorization": "Bearer TOK"}
    with mock.patch.object(foto.requests, "get", return_value=_Resp()) as g:
        foto.descargar_erp("https://archivos.unemi.edu.ec/a.jpg", "TOK", base)
        assert g.call_args.kwargs["headers"] == {}          # otro subdominio: sin token
    assert foto.descargar_erp("https://malo.com/a.jpg", "TOK", base)[0] is None

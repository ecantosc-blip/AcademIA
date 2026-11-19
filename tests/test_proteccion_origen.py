"""Mitigacion CSRF por validacion de origen (web/app.py: _origen_seguro / _proteger_origen).

Esta prueba existe porque docs/CHECKLIST_SEGURIDAD_REGRESION.md marcaba la verificacion
de esta proteccion como un paso MANUAL ("confirmar que un POST con Origin malicioso
responde 403"). Se automatiza aqui para que quede comprobado en cada corrida de
pytest/CI, no solo en una revision manual puntual.

Se usa /login (POST, sin autenticacion previa) como endpoint de prueba: con datos
vacios, si la peticion pasa el filtro de origen, Moodle responde "invalido" (estado
200 con el formulario); si el filtro la rechaza, el codigo es 403 y el cuerpo nunca
llega a tocar login_submit().
"""
import pytest


@pytest.fixture
def cliente(app_web):
    return app_web.app.test_client()


def _post_login(cliente, **headers):
    return cliente.post("/login", data={}, headers=headers)


def test_post_sin_cabeceras_de_origen_no_se_rechaza(cliente):
    """Clientes que no son navegador (o navegadores viejos) no mandan Origin/Referer/
    Sec-Fetch-Site: no se puede comprobar su origen, asi que se dejan pasar (ver el
    docstring de _origen_seguro en web/app.py)."""
    r = _post_login(cliente)
    assert r.status_code != 403


def test_post_mismo_origen_no_se_rechaza(cliente):
    r = _post_login(cliente, Origin="http://localhost")
    assert r.status_code != 403


def test_post_referer_mismo_origen_no_se_rechaza(cliente):
    r = _post_login(cliente, Referer="http://localhost/login")
    assert r.status_code != 403


def test_post_origen_cruzado_se_rechaza(cliente):
    r = _post_login(cliente, Origin="https://sitio-malicioso.invalid")
    assert r.status_code == 403
    assert "origen no permitido" in r.get_data(as_text=True)


def test_post_origen_null_se_rechaza(cliente):
    """Origin: null aparece en iframes en sandbox, redirecciones file:// y algunos
    clientes manipulados: se trata siempre como no confiable."""
    r = _post_login(cliente, Origin="null")
    assert r.status_code == 403


def test_post_referer_cruzado_se_rechaza(cliente):
    r = _post_login(cliente, Referer="https://sitio-malicioso.invalid/pagina")
    assert r.status_code == 403


def test_post_sec_fetch_site_cruzado_se_rechaza_aunque_el_origen_coincida(cliente):
    """Sec-Fetch-Site lo pone el navegador mismo (no se puede falsear desde JS), asi
    que si dice cross-site se rechaza sin mirar Origin, incluso si alguien logro
    falsear ese encabezado."""
    r = _post_login(cliente, **{"Sec-Fetch-Site": "cross-site"}, Origin="http://localhost")
    assert r.status_code == 403


def test_get_nunca_se_rechaza_por_origen(cliente):
    """La proteccion solo aplica a metodos que mutan datos (POST/PUT/PATCH/DELETE)."""
    r = cliente.get("/login", headers={"Origin": "https://sitio-malicioso.invalid"})
    assert r.status_code != 403

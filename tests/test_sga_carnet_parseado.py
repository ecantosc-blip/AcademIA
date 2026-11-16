"""SGAClient.get_carnet_parseado: el ERP ha contestado el carne de mas de una forma.

El 15/nov/2026 se confirmo con una cuenta real (carrera de Grado) que el ERP entrega el
PDF real del carne -- el mismo que arma su propio boton "Imprimir en PDF" -- como
{"data": {"pdf": "https://.../archivo.pdf"}}, plano. Antes de eso solo se habia visto la
forma anidada ({"data": {"eCarnet": {...}}}) y, cuando no hay PDF, los datos sueltos del
"carne digital" en {"data": {"eCarnetDigital": {...}}}. get_carnet_parseado debe entender
las tres sin romper ninguna.
"""
from unittest import mock

from sga.client import SGAClient


def _cliente_con(respuesta):
    c = SGAClient(base_url="https://sga.unemi.edu.ec", access_token="t", refresh_token="r")
    with mock.patch.object(c, "get_carnet", return_value=respuesta):
        return c.get_carnet_parseado()


def test_forma_plana_data_pdf_es_el_caso_real_confirmado_con_cuenta_de_grado():
    """La forma que de verdad entrega el ERP para una cuenta con PDF ya armado."""
    info = _cliente_con({"data": {"pdf": "https://sga.unemi.edu.ec/media//carnet/x/y/z.pdf"}})
    assert info["pdf_url"] == "https://sga.unemi.edu.ec/media//carnet/x/y/z.pdf"


def test_forma_anidada_ecarnet_pdf_sigue_funcionando():
    """La forma antigua (antes de la migracion de nov/2026) no debe dejar de funcionar."""
    info = _cliente_con({"data": {"eCarnet": {"pdf": "https://sga.unemi.edu.ec/media/viejo.pdf",
                                               "png_anverso": "https://sga.unemi.edu.ec/media/a.png",
                                               "activo": True}}})
    assert info["pdf_url"] == "https://sga.unemi.edu.ec/media/viejo.pdf"
    assert info["imagen_url"] == "https://sga.unemi.edu.ec/media/a.png"
    assert info["activo"] is True


def test_forma_plana_gana_si_por_alguna_razon_vinieran_las_dos():
    """Si el ERP llegara a mandar ambas formas a la vez, se prioriza la del PDF real plano."""
    info = _cliente_con({"data": {"pdf": "https://sga.unemi.edu.ec/media/nuevo.pdf",
                                   "eCarnet": {"pdf": "https://sga.unemi.edu.ec/media/viejo.pdf"}}})
    assert info["pdf_url"] == "https://sga.unemi.edu.ec/media/nuevo.pdf"


def test_sin_pdf_cae_a_los_datos_del_carne_digital():
    """Cuando el ERP no tiene PDF para esta cuenta, se usan los datos sueltos de eCarnetDigital
    (los que arma el generador propio en core/carnet_pdf)."""
    info = _cliente_con({"data": {"eCarnetDigital": {"nombres": "Kleber", "apellidos": "Solis",
                                                      "cedula": "0943293290", "carrera": "Enfermería",
                                                      "qr": "data:image/png;base64,abc"}}})
    assert info["pdf_url"] is None
    assert info["nombres"] == "Kleber" and info["cedula"] == "0943293290"
    assert info["qr_data_uri"] == "data:image/png;base64,abc"


def test_sin_nada_todos_los_campos_quedan_vacios():
    """Cuenta sin carne de ningun tipo (ej. propedeutico): no se inventa nada."""
    info = _cliente_con({"data": {}})
    assert info["pdf_url"] is None and info["nombres"] is None and info["cedula"] is None

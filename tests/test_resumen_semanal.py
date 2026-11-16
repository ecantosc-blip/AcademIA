"""
Resumen semanal con IA (core/resumen_semanal.py): el modelo redacta, pero solo
con los hechos que se le entregan (ya calculados por core/tareas.py y
core/notas_moodle.py, el mismo criterio que usa el resto de la app). Si la IA
no responde, el resumen no se rompe: se muestran los hechos sin redactar.
"""
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from core import tiempo
from core.resumen_semanal import generar
from db.setup import get_connection

AHORA = "2026-10-25T10:00:00"      # 15:00 UTC = 10:00 en Ecuador


@pytest.fixture
def datos(bd_vacia, monkeypatch):
    monkeypatch.setattr(tiempo, "_reloj", lambda: datetime(2026, 10, 25, 15, 0, 0, tzinfo=timezone.utc))
    c = get_connection()
    c.executescript("""
        INSERT INTO estudiante(id,nombre) VALUES (77,'Ana');
        INSERT INTO materia(id,nombre,moodle_course_id) VALUES (1,'CALCULO',1),(2,'FISICA',2);
        INSERT INTO matricula(estudiante_id,materia_id,estado) VALUES (77,1,'en_curso'),(77,2,'en_curso');
        INSERT INTO tarea(id,materia_id,moodle_assign_id,nombre,fecha_entrega) VALUES
          (1,1,11,'Deber de limites','2026-10-27T10:00:00'),
          (2,1,12,'Taller ya vencido','2026-10-20T10:00:00'),
          (3,2,13,'Informe entregado','2026-10-26T10:00:00'),
          (4,2,14,'Quiz sin verificar','2026-10-26T12:00:00');
        INSERT INTO entrega(tarea_id,estudiante_id,estado,verificada_en) VALUES
          (1,77,'sin_entregar','2026-10-18T10:00:00'),
          (2,77,'sin_entregar','2026-10-18T10:00:00'),
          (3,77,'entregada','2026-10-24T09:00:00');
        INSERT INTO evento(estudiante_id,nombre,tipo,fecha) VALUES (77,'Examen de fisica','examen','2026-10-28T08:00:00');
        INSERT INTO componente_nota(matricula_id,nombre_item,calificacion,porcentaje) VALUES
          (1,'Total del curso','62,0',62.0),(2,'Total del curso','90,0',90.0);
    """)
    c.commit()
    yield c
    c.close()


class _ClienteFalso:
    """Nunca sale a la red: siempre llama a la herramienta con un resultado fijo."""
    def __init__(self, resultado=None, fallar=False):
        self.resultado = resultado or {
            "completado": ["Informe entregado (Fisica)"],
            "pendiente": ["Deber de limites (Calculo) - vence 2026-10-27"],
            "proximas_evaluaciones": ["Examen de fisica - 2026-10-28"],
            "sugerencias": ["Entrega el taller vencido de Calculo cuanto antes."],
            "mensaje": "Vas bien esta semana: entregaste el informe y te queda un deber por hacer.",
        }
        self.fallar = fallar
        self.llamadas = []
        self.messages = self

    def create(self, **kw):
        self.llamadas.append(kw)
        if self.fallar:
            raise RuntimeError("la IA no respondio")
        return NS(content=[NS(type="tool_use", name="generar_resumen", input=self.resultado)])


def test_usa_solo_los_hechos_y_fuerza_la_herramienta(datos):
    cliente = _ClienteFalso()
    r = generar(datos, 77, client=cliente, ahora=AHORA)
    assert r["generado"] is True
    assert r["completado"] == ["Informe entregado (Fisica)"]
    assert r["mensaje"].startswith("Vas bien")
    llamada = cliente.llamadas[0]
    assert llamada["tool_choice"] == {"type": "tool", "name": "generar_resumen"}
    assert llamada["tools"][0]["name"] == "generar_resumen"


def test_los_hechos_distinguen_completado_pendiente_vencida_y_sin_verificar(datos):
    cliente = _ClienteFalso()
    r = generar(datos, 77, client=cliente, ahora=AHORA)
    h = r["hechos"]
    assert h["completado_confirmado_esta_semana"] == ["Informe entregado (FISICA)"]
    assert "Deber de limites (CALCULO) - vence 2026-10-27" in h["pendiente_confirmado"]
    assert h["vencidas_sin_entregar"] == ["Taller ya vencido (CALCULO) - vencio 2026-10-20"]
    assert h["sin_verificar_moodle"] == ["Quiz sin verificar (FISICA)"]
    assert h["evaluaciones_proximos_7_dias"] == ["Examen de fisica - 2026-10-28"]


def test_solo_entra_como_completado_lo_verificado_dentro_de_los_7_dias(datos):
    """Una entrega confirmada hace mas de 7 dias no cuenta como 'esta semana'."""
    datos.execute("update entrega set verificada_en = '2026-09-01T09:00:00' where tarea_id = 3")
    datos.commit()
    cliente = _ClienteFalso()
    r = generar(datos, 77, client=cliente, ahora=AHORA)
    assert r["hechos"]["completado_confirmado_esta_semana"] == []


def test_prioriza_la_materia_con_menor_porcentaje_de_moodle(datos):
    cliente = _ClienteFalso()
    r = generar(datos, 77, client=cliente, ahora=AHORA)
    assert r["hechos"]["materias_con_menor_porcentaje_moodle"] == ["CALCULO: 62%", "FISICA: 90%"]


def test_si_la_ia_falla_no_rompe_el_resumen_y_muestra_los_hechos_crudos(datos):
    cliente = _ClienteFalso(fallar=True)
    r = generar(datos, 77, client=cliente, ahora=AHORA)
    assert r["generado"] is False
    assert r["completado"] == ["Informe entregado (FISICA)"]
    assert "Deber de limites" in r["pendiente"][0]
    assert r["sugerencias"] == []
    assert "no se pudo" in r["mensaje"].lower() or "sin redactar" in r["mensaje"].lower()


def test_las_sugerencias_del_modelo_se_recortan_a_un_maximo_de_tres(datos):
    cliente = _ClienteFalso(resultado={
        "completado": [], "pendiente": [], "proximas_evaluaciones": [],
        "sugerencias": ["uno", "dos", "tres", "cuatro"], "mensaje": "x"})
    r = generar(datos, 77, client=cliente, ahora=AHORA)
    assert r["sugerencias"] == ["uno", "dos", "tres"]

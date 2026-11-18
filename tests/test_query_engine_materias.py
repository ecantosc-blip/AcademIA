"""
QueryEngine.materias_actuales() y rendimiento_actual() (core/query_engine.py):
el chat debe poder contestar "¿qué materias curso ahora?" y "¿cómo voy en mis
materias?" sin tener que preguntar el semestre, usando SOLO lo que el
estudiante cursa en_curso ahora mismo -- no todo su historial.
"""
import pytest

from core.query_engine import QueryEngine
from db.setup import get_connection


@pytest.fixture
def datos(bd_vacia):
    c = get_connection()
    c.executescript("""
        INSERT INTO estudiante(id,nombre) VALUES (77,'Ana');
        INSERT INTO materia(id,nombre,moodle_course_id) VALUES
          (1,'CALCULO',1),(2,'FISICA',2),(3,'ALGEBRA LINEAL',3),(4,'QUIMICA',4);
        -- Calculo, Fisica y Quimica: en curso ahora. Algebra: ya aprobada un semestre anterior.
        INSERT INTO matricula(id,estudiante_id,materia_id,estado,nota_final,asistencia) VALUES
          (10,77,1,'en_curso',NULL,90),
          (11,77,2,'en_curso',NULL,85),
          (12,77,3,'aprobada',88,95),
          (13,77,4,'en_curso',NULL,80);
        -- Calculo: componentes de Moodle ya sincronizados, con el formato REAL que entrega Moodle
        -- (el total de cada bloque se llama "Total N1", no "N1"; ver moodle/client.py). Se agrega
        -- tambien una fila de tarea individual ("N2 - Tarea 1") y un total sin calcular ("Total N3"
        -- = "-") para probar que no se confunden con los totales de bloque. Fisica: todavia sin
        -- ningun componente sincronizado.
        INSERT INTO componente_nota(matricula_id,nombre_item,calificacion) VALUES
          (10,'Total N1','8,5'),(10,'Total N2','9,0'),
          (10,'N2 - Tarea 1','9,0'),(10,'Total N3','-');
        -- Quimica: mismos valores reales que confirmo el usuario con pantallazos de Moodle para
        -- "SISTEMA DE AUTOMATIZACIÓN INDUSTRIAL" (Total EXP1 = 15,00, peso 15; Total N3 = 5,00, peso
        -- 10). Sirve para probar que un componente con peso distinto de 10 ya NO se reescala.
        INSERT INTO componente_nota(matricula_id,nombre_item,calificacion) VALUES
          (13,'Total N1','-'),(13,'Total N2','-'),
          (13,'Total EXP1','15,00'),(13,'Total N3','5,00');
    """)
    c.commit()
    yield c
    c.close()


def test_materias_actuales_no_incluye_materias_de_semestres_ya_cursados(datos):
    """Bug real: antes esta consulta no filtraba por estado y mezclaba Algebra (ya aprobada)
    con las materias que de verdad se cursan ahora."""
    texto = QueryEngine(77).materias_actuales()
    assert "CALCULO" in texto and "FISICA" in texto
    assert "ALGEBRA" not in texto


def test_materias_actuales_sin_nada_en_curso_lo_dice(datos):
    datos.execute("update matricula set estado='aprobada' where estudiante_id=77")
    datos.commit()
    texto = QueryEngine(77).materias_actuales()
    assert "No tienes materias en curso" in texto


def test_rendimiento_actual_calcula_con_componentes_de_moodle_no_con_el_erp(datos):
    """Calculo: Total N1=8,5 y Total N2=9,0 (de 10 c/u) ya calificados, Total N3='-' (vale 10 pts
    pero todavia sin calificar) -> 8.5 + 9.0 = 17.5 obtenidos de 30 posibles (N1+N2+N3). La fila de
    tarea individual "N2 - Tarea 1" NO debe contarse aparte (ya esta incluida en "Total N2"; solo se
    usan los totales de bloque, igual que hace Moodle). No debe usarse mat.nota_final del ERP (que
    aquí es NULL)."""
    texto = QueryEngine(77).rendimiento_actual()
    assert "CALCULO: 17.5/30" in texto
    assert "Sin componentes de Moodle sincronizados todavía: FISICA" in texto
    assert "ALGEBRA" not in texto    # no esta en curso


def test_rendimiento_actual_no_reescala_componentes_con_peso_distinto_de_10(datos):
    """Caso real reportado por el usuario (pantallazos de Moodle): Total EXP1 = 15,00 (peso 15) y
    Total N3 = 5,00 (peso 10). Antes del fix esto se reescalaba como si viniera sobre 10
    (EXP1 -> (15/10)*15 = 22.5, mas que el maximo posible de 15). Ahora se usa el valor tal cual:
    15 + 5 = 20 obtenidos de 45 posibles (N1+N2+EXP1+N3)."""
    texto = QueryEngine(77).rendimiento_actual()
    assert "QUIMICA: 20.0/45" in texto


def test_rendimiento_actual_sin_materias_en_curso_lo_dice(datos):
    datos.execute("update matricula set estado='aprobada' where estudiante_id=77")
    datos.commit()
    texto = QueryEngine(77).rendimiento_actual()
    assert "No tienes materias en curso" in texto


def test_rendimiento_materia_reconoce_los_totales_de_bloque_reales_de_moodle(datos):
    """Antes nunca habia una prueba para rendimiento_materia(): el mismo bug de _componentes_de()
    (comparar contra 'N1' en vez de 'Total N1') lo afectaba tambien a el, sin que ningun test lo
    hubiera detectado."""
    texto = QueryEngine(77).rendimiento_materia("CALCULO")
    assert "- N1: 8.5/10 pts" in texto
    assert "- N2: 9.0/10 pts" in texto
    assert "- N3: sin calificar (vale 10 pts)" in texto
    assert "Total: 17.5 / 30 puntos evaluados hasta ahora." in texto


def test_rendimiento_materia_no_reescala_componentes_con_peso_distinto_de_10(datos):
    texto = QueryEngine(77).rendimiento_materia("QUIMICA")
    assert "- EXP1: 15.0/15 pts" in texto
    assert "- N3: 5.0/10 pts" in texto
    assert "Total: 20.0 / 45 puntos evaluados hasta ahora." in texto


def test_rendimiento_materia_sin_componentes_sincronizados_lo_dice(datos):
    texto = QueryEngine(77).rendimiento_materia("FISICA")
    assert "No tengo calificaciones por componente de FISICA sincronizadas todavía." in texto


# --- prioridad_estudio(): combina tareas atrasadas + promedio de Moodle, mismo peso (a pedido
# explicito del usuario). Ver el docstring del metodo en core/query_engine.py para el porque. ---

@pytest.fixture
def datos_prioridad(bd_vacia):
    c = get_connection()
    c.executescript("""
        INSERT INTO estudiante(id,nombre) VALUES (77,'Ana');
        INSERT INTO materia(id,nombre,moodle_course_id) VALUES
          (1,'CALCULO',1),(2,'FISICA',2),(3,'QUIMICA',3);
        INSERT INTO matricula(id,estudiante_id,materia_id,estado) VALUES
          (10,77,1,'en_curso'),(11,77,2,'en_curso'),(12,77,3,'en_curso');
        -- Calculo: promedio bajo (40%) y sin tareas atrasadas.
        -- Fisica: promedio alto (90%) pero 2 tareas atrasadas.
        -- Quimica: sin componentes de Moodle sincronizados (no se puede priorizar).
        INSERT INTO componente_nota(matricula_id,nombre_item,calificacion,porcentaje) VALUES
          (10,'Total del curso','4,0','40'),
          (11,'Total del curso','9,0','90');
        INSERT INTO tarea(id,materia_id,moodle_assign_id,nombre,fecha_entrega) VALUES
          (100,2,900,'Tarea vencida 1','2026-01-01T10:00:00'),
          (101,2,901,'Tarea vencida 2','2026-01-02T10:00:00');
        INSERT INTO entrega(tarea_id,estudiante_id,estado,verificada_en) VALUES
          (100,77,'sin_entregar','2026-01-03T10:00:00'),
          (101,77,'sin_entregar','2026-01-03T10:00:00');
    """)
    c.commit()
    yield c
    c.close()


def test_prioridad_estudio_combina_tareas_atrasadas_y_promedio_con_el_mismo_peso(datos_prioridad):
    texto = QueryEngine(77).prioridad_estudio()
    # Calculo (0 atrasadas, 40%) y Fisica (2 atrasadas, 90%) quedan EMPATADAS en el puesto
    # combinado (Borda): cada una gana en un factor y pierde en el otro. El desempate es por
    # nombre, asi que Calculo sale primero.
    assert texto.index("1. CALCULO") < texto.index("2. FISICA")
    assert "0 tareas vencidas" in texto and "promedio 40% en Moodle" in texto
    assert "2 tareas vencidas sin entregar" in texto and "promedio 90% en Moodle" in texto
    assert "Sin promedio de Moodle sincronizado, no se pudieron incluir en el orden: QUIMICA" in texto


def test_prioridad_estudio_sin_ningun_promedio_lo_dice_sin_inventar(datos_prioridad):
    datos_prioridad.execute("DELETE FROM componente_nota")
    datos_prioridad.commit()
    texto = QueryEngine(77).prioridad_estudio()
    assert "no puedo calcular una prioridad de estudio" in texto
    assert "FISICA (2 tareas vencidas sin entregar)" in texto


def test_prioridad_estudio_sin_materias_en_curso_lo_dice(datos_prioridad):
    datos_prioridad.execute("update matricula set estado='aprobada' where estudiante_id=77")
    datos_prioridad.commit()
    texto = QueryEngine(77).prioridad_estudio()
    assert "No tienes materias en curso" in texto

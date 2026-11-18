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
          (1,'CALCULO',1),(2,'FISICA',2),(3,'ALGEBRA LINEAL',3);
        -- Calculo y Fisica: en curso ahora. Algebra: ya aprobada un semestre anterior.
        INSERT INTO matricula(id,estudiante_id,materia_id,estado,nota_final,asistencia) VALUES
          (10,77,1,'en_curso',NULL,90),
          (11,77,2,'en_curso',NULL,85),
          (12,77,3,'aprobada',88,95);
        -- Calculo: componentes de Moodle ya sincronizados, con el formato REAL que entrega Moodle
        -- (el total de cada bloque se llama "Total N1", no "N1"; ver moodle/client.py). Se agrega
        -- tambien una fila de tarea individual ("N2 - Tarea 1") y un total sin calcular ("Total N3"
        -- = "-") para probar que no se confunden con los totales de bloque. Fisica: todavia sin
        -- ningun componente sincronizado.
        INSERT INTO componente_nota(matricula_id,nombre_item,calificacion) VALUES
          (10,'Total N1','8,5'),(10,'Total N2','9,0'),
          (10,'N2 - Tarea 1','9,0'),(10,'Total N3','-');
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
    assert "N1: 8.5/10 → 8.5/10 pts" in texto
    assert "N2: 9.0/10 → 9.0/10 pts" in texto
    assert "N3: sin calificar (vale 10 pts)" in texto
    assert "Total: 17.5 / 30 puntos evaluados hasta ahora." in texto


def test_rendimiento_materia_sin_componentes_sincronizados_lo_dice(datos):
    texto = QueryEngine(77).rendimiento_materia("FISICA")
    assert "No tengo calificaciones por componente de FISICA sincronizadas todavía." in texto

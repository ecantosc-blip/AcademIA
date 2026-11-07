import sqlite3
import os
from datetime import datetime


def _columna_existe(conn, tabla, columna):
    cur = conn.execute(f"PRAGMA table_info({tabla})")
    return any(row["name"] == columna for row in cur.fetchall())


def _tablas(conn):
    return {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _es_esquema_legado(conn) -> bool:
    """
    Detecta una base creada con el esquema ANTERIOR (sin normalizar): tenia
    rubro_financiero / nota, docente_nombre dentro de materia, entregada
    dentro de tarea, o columnas de finanzas/carne en estudiante.
    """
    tablas = _tablas(conn)
    if not tablas:
        return False
    if "rubro_financiero" in tablas or "nota" in tablas:
        return True
    if "materia" in tablas and _columna_existe(conn, "materia", "docente_nombre"):
        return True
    if "tarea" in tablas and _columna_existe(conn, "tarea", "entregada"):
        return True
    if "estudiante" in tablas and _columna_existe(conn, "estudiante", "total_adeudado"):
        return True
    return False


def _reiniciar_esquema_legado(conn):
    """
    Los datos de estas tablas son una COPIA sincronizada desde Moodle/ERP
    (no se capturan a mano), asi que al pasar al esquema 3FN se descartan
    las tablas viejas y se recrean; el estudiante vuelve a llenarlas con el
    boton de sincronizar. Las sesiones web tambien se descartan (hay que
    volver a iniciar sesion).
    """
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    for tabla in sorted(_tablas(conn) - {"schema_version"}):
        conn.execute(f'DROP TABLE IF EXISTS "{tabla}"')
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    print("  Esquema anterior detectado: se recreo con el esquema normalizado (3FN). "
          "Vuelve a sincronizar tus datos.")


def _migracion_1_esquema_3fn(conn):
    """
    Migracion 1: esquema normalizado (3FN). Las tablas ya las crea
    schema.sql; aqui solo queda registrado el punto de partida. Los cambios
    futuros del esquema se agregan como migraciones 2, 3, ...
    """
    return None


# ---------------------------------------------------------------------------
# MIGRACIONES VERSIONADAS
# Para cambiar el esquema (agregar/quitar columnas o tablas, normalizar...):
#   1. Escribe una funcion _migracion_N_...(conn) que haga el cambio.
#   2. Agregala AL FINAL de MIGRACIONES con el siguiente numero.
# init_db() aplica solo las que faltan, en orden, y las anota en la tabla
# schema_version. Nunca edites ni borres una migracion ya aplicada.
# ---------------------------------------------------------------------------
def _migracion_2_practica(conn):
    # La tabla `practica` se crea en schema.sql (CREATE TABLE IF NOT EXISTS), asi
    # que en una base existente ya quedo creada; esta migracion solo deja
    # constancia de la version.
    pass


def _migracion_3_estado_de_entrega(conn):
    """
    Migracion 3: `entrega` pasa de (entregada 0/1 obligatorio) a (estado, verificada_en).
    Con 0/1 obligatorio no se podia expresar "no se sabe", y como la tabla nunca se
    llenaba, todas las tareas parecian pendientes. Ahora la ausencia de fila es
    "sin verificar". Filas antiguas: 1 -> 'entregada', 0 -> 'sin_entregar' (fecha de
    verificacion desconocida). Todo ocurre en una transaccion: si falla, no cambia nada.
    """
    if "entrega" not in _tablas(conn) or not _columna_existe(conn, "entrega", "entregada"):
        return None          # base nueva (schema.sql ya la creo con la forma nueva)
    conn.commit()
    conn.execute("BEGIN")
    try:
        conn.execute("DROP TABLE IF EXISTS entrega_nueva")
        conn.execute("""
            CREATE TABLE entrega_nueva (
                tarea_id INTEGER NOT NULL,
                estudiante_id INTEGER NOT NULL,
                estado TEXT NOT NULL CHECK(estado IN ('entregada', 'borrador', 'sin_entregar')),
                verificada_en TEXT,
                PRIMARY KEY (tarea_id, estudiante_id),
                FOREIGN KEY (tarea_id) REFERENCES tarea(id) ON DELETE CASCADE,
                FOREIGN KEY (estudiante_id) REFERENCES estudiante(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            INSERT INTO entrega_nueva (tarea_id, estudiante_id, estado, verificada_en)
            SELECT tarea_id, estudiante_id,
                   CASE WHEN entregada = 1 THEN 'entregada' ELSE 'sin_entregar' END, NULL
            FROM entrega
        """)
        conn.execute("DROP TABLE entrega")
        conn.execute("ALTER TABLE entrega_nueva RENAME TO entrega")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entrega_estudiante ON entrega(estudiante_id)")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migracion_4_detector_de_cambios(conn):
    # Las tablas `foto_estado` y `cambio` se crean en schema.sql (CREATE TABLE IF NOT EXISTS),
    # asi que una base existente las recibe sin tocar ningun dato. Esta migracion solo deja
    # constancia de la version. Un estudiante que ya tenia datos no recibe cambios falsos:
    # su primera sincronizacion despues de actualizar solo guarda la foto de partida.
    pass


def _migracion_5_plan_y_alertas(conn):
    # Las tablas preferencia, dificultad_materia, plan_excluido y alerta se crean en schema.sql
    # (CREATE TABLE IF NOT EXISTS): una base existente las recibe sin tocar ningun dato.
    pass


def _migracion_6_plan_conversacional(conn):
    # Las tablas franja_libre, actividad, telegram_vinculo y recordatorio_enviado se crean en
    # schema.sql (CREATE TABLE IF NOT EXISTS): una base existente las recibe sin tocar datos.
    pass


def _migracion_7_bloques_fijos(conn):
    # La tabla bloque_fijo se crea en schema.sql (CREATE TABLE IF NOT EXISTS): sin tocar datos.
    pass


MIGRACIONES = [
    (1, "Esquema normalizado 3FN (sin finanzas ni carne)", _migracion_1_esquema_3fn),
    (2, "Espacio de estudio: tabla practica", _migracion_2_practica),
    (3, "Estado de entrega verificable (ausencia de fila = sin verificar)", _migracion_3_estado_de_entrega),
    (4, "Detector de cambios: tablas foto_estado y cambio", _migracion_4_detector_de_cambios),
    (5, "Plan de estudio y alertas: preferencia, dificultad_materia, plan_excluido, alerta", _migracion_5_plan_y_alertas),
    (6, "Plan conversacional y recordatorios: franja_libre, actividad, telegram_vinculo, recordatorio_enviado", _migracion_6_plan_conversacional),
    (7, "Bloques de estudio fijados por la persona: bloque_fijo", _migracion_7_bloques_fijos),
]


def _aplicar_migraciones(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            descripcion TEXT,
            aplicada TEXT NOT NULL
        )
    """)
    aplicadas = {fila["version"] for fila in conn.execute("SELECT version FROM schema_version")}
    for version, descripcion, funcion in sorted(MIGRACIONES, key=lambda m: m[0]):
        if version in aplicadas:
            continue
        funcion(conn)
        conn.execute(
            "INSERT INTO schema_version (version, descripcion, aplicada) VALUES (?, ?, ?)",
            (version, descripcion, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        print(f"  Migracion {version} aplicada: {descripcion}")


def get_connection(db_path: str = None):
    db_path = db_path or os.getenv("DB_PATH", "./unemi_assistant.db")
    # check_same_thread=False: el servidor web atiende cada peticion en un hilo
    # distinto. timeout: si otra conexion esta escribiendo, se espera hasta 10 s
    # en vez de fallar al instante con "database is locked".
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    # SQLite no hace cumplir las claves foraneas salvo que se active.
    conn.execute("PRAGMA foreign_keys = ON")
    # Modo WAL: las lecturas ya no se bloquean con una escritura (y viceversa),
    # que es lo que permite atender a varios estudiantes a la vez. Es una
    # propiedad del archivo: queda activada para todas las conexiones.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(db_path: str = None):
    conn = get_connection(db_path)
    if _es_esquema_legado(conn):
        _reiniciar_esquema_legado(conn)
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    _aplicar_migraciones(conn)
    conn.close()
    print(f"Base de datos inicializada en: {db_path or os.getenv('DB_PATH')}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    init_db()

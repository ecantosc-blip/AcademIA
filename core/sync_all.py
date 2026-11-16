"""
Sincronizacion completa: trae datos de Moodle Y del SGA, y los combina en
la base de datos unificada. Reemplaza a sync_moodle.py (que se deja como
referencia del primer paso de validacion, pero para uso real conviene
correr este script).
"""
import os
import sys
import unicodedata

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moodle.client import build_client_from_env as build_moodle_client, limpiar_nombre_curso
from sga.client import build_client_from_env as build_sga_client
from db.setup import get_connection
from core.nombres import coincide, nivel_a_numero
from core.privacidad import depurar
from core.entregas import sincronizar_entregas
from core import cambios as detector_cambios
from core.tiempo import ahora_iso, timestamp_a_iso


def _normalizar(texto: str) -> str:
    """Quita acentos y pasa a minusculas - mismo criterio que query_engine,
    para que la coincidencia de nombres de materia entre SGA y Moodle no
    falle por diferencias de acentuacion."""
    if not texto:
        return ""
    sin_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sin_acentos.lower()


def _enmascarar(texto: str) -> str:
    """Para diagnostico: quita el nombre del alumno (todo antes de la primera
    coma) y cualquier numero largo (cedula) antes de imprimir un texto."""
    import re
    texto = texto or ""
    if ", " in texto:
        texto = "[NOMBRE] , " + texto.split(", ", 1)[1]
    return re.sub(r"\d{8,}", "[#]", texto)


def _buscar_materia_id(cursor, nombre_buscado: str, estudiante_id: int = None):
    """
    Busca el id de materia que mejor coincide con nombre_buscado, ignorando
    acentos/mayusculas y aceptando coincidencia parcial en cualquier
    direccion.

    IMPORTANTE (correccion de un bug real de aislamiento entre usuarios):
    si se pasa estudiante_id, la busqueda se restringe SOLO a las materias
    en las que ese estudiante ya esta matriculado (via la tabla matricula).
    Sin este filtro, la busqueda por nombre corria sobre la tabla materia
    COMPLETA (compartida entre todos los usuarios), lo que podia hacer que
    el horario/notas de un estudiante se guardaran por error en la materia
    de otro estudiante distinto con nombre parecido.
    """
    if not nombre_buscado:
        return None
    query_norm = _normalizar(nombre_buscado)

    if estudiante_id is not None:
        cursor.execute("""
            SELECT DISTINCT m.id, m.nombre
            FROM materia m
            JOIN matricula mat ON mat.materia_id = m.id
            WHERE mat.estudiante_id = ?
        """, (estudiante_id,))
    else:
        cursor.execute("SELECT id, nombre FROM materia")

    mejor_id = None
    mejor_longitud = 0
    for row in cursor.fetchall():
        if not row["nombre"]:
            continue
        nombre_norm = _normalizar(row["nombre"])
        if nombre_norm == query_norm:
            return row["id"]            # coincidencia exacta: no hay nada mejor
        if coincide(nombre_norm, query_norm) and len(nombre_norm) > mejor_longitud:
            mejor_id = row["id"]
            mejor_longitud = len(nombre_norm)
    return mejor_id


def _registrar_cambios(conn, estudiante_id: int, plataforma: str) -> int:
    """
    Detector de cambios: se llama SOLO cuando la sincronizacion de esa plataforma termino bien
    y sus datos ya estan guardados. Si el detector falla, la sincronizacion sigue siendo
    exitosa (solo se pierde esa comparacion) y no queda nada a medias.
    """
    try:
        n = detector_cambios.registrar(conn, estudiante_id, plataforma)
        conn.commit()
        if n:
            print(f"[{plataforma}] Novedades detectadas: {n}")
        return n
    except Exception as e:
        conn.rollback()
        print(f"[{plataforma}] Aviso: no se pudieron comparar cambios: {depurar(e)}")
        return 0


def sync(estudiante_id: int = 1, db_path: str = None,
         moodle_client=None, moodle_username: str = None, moodle_password: str = None, moodle_service: str = None,
         sga_client=None, sga_alumno_id: str = None):
    """
    Sincroniza los datos de un estudiante especifico.

    Se puede llamar de dos formas:
    1. Sin argumentos (uso por consola, como hasta ahora): lee todo de .env,
       usa estudiante_id=1. Comportamiento identico al original.
    2. Con argumentos explicitos (uso desde la app web multi-usuario):
       se le pasa el estudiante_id real de esa persona, un cliente de
       Moodle/SGA ya listo (o sus credenciales), sin depender de variables
       de entorno globales - asi cada usuario de la web sincroniza solo
       sus propios datos, con sus propias credenciales de sesion.
    """
    from dotenv import load_dotenv
    load_dotenv()

    conn = get_connection(db_path)
    cursor = conn.cursor()

    # Garantiza que exista la fila del estudiante antes de registrar nada
    # (sesion_extraccion, matricula... apuntan a ella por clave foranea).
    cursor.execute(
        "INSERT OR IGNORE INTO estudiante (id, nombre) VALUES (?, 'Estudiante')",
        (estudiante_id,),
    )
    conn.commit()

    # ---------- Parte 1: Moodle (cursos, tareas, eventos, calificaciones) ----------
    moodle = moodle_client or build_moodle_client()
    username = moodle_username or os.getenv("MOODLE_USERNAME")
    password = moodle_password or os.getenv("MOODLE_PASSWORD")
    service = moodle_service or os.getenv("MOODLE_SERVICE", "moodle_mobile_app")

    try:
        # Autenticacion por token: si el cliente ya viene con el token del
        # estudiante (flujo web), NO se vuelve a hacer login con usuario y
        # contraseña. Solo se hace login cuando no hay token (uso por consola
        # con credenciales del .env, como en el prototipo original).
        if not getattr(moodle, "token", None):
            moodle.login(username, password, service)
        info = moodle.get_site_info()

        cursor.execute("""
            INSERT INTO estudiante (id, nombre, moodle_user_id, fecha_sincronizacion)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                nombre = excluded.nombre,
                moodle_user_id = excluded.moodle_user_id,
                fecha_sincronizacion = excluded.fecha_sincronizacion
        """, (estudiante_id, info["fullname"], info["userid"], ahora_iso()))

        courses = moodle.get_courses()
        course_ids = []
        materia_id_por_moodle_id = {}

        for c in courses:
            course_ids.append(c["id"])
            nombre_limpio = limpiar_nombre_curso(c["fullname"])
            cursor.execute("""
                INSERT INTO materia (moodle_course_id, nombre)
                VALUES (?, ?)
                ON CONFLICT(moodle_course_id) DO UPDATE SET nombre = excluded.nombre
            """, (c["id"], nombre_limpio))

            cursor.execute("SELECT id FROM materia WHERE moodle_course_id = ?", (c["id"],))
            materia_id = cursor.fetchone()["id"]
            materia_id_por_moodle_id[c["id"]] = materia_id

            cursor.execute("""
                SELECT id FROM matricula WHERE estudiante_id = ? AND materia_id = ?
            """, (estudiante_id, materia_id))
            row = cursor.fetchone()
            if not row:
                cursor.execute("""
                    INSERT INTO matricula (estudiante_id, materia_id, estado)
                    VALUES (?, ?, 'en_curso')
                """, (estudiante_id, materia_id))

            # Docente(s) del curso: cada uno es una fila de `docente`, unida a
            # la materia por `materia_docente` (relacion N:M, 1FN).
            profesores = moodle.get_teachers(c["id"])
            cursor.execute("DELETE FROM materia_docente WHERE materia_id = ?", (materia_id,))
            for p in profesores or []:
                if not p.get("nombre"):
                    continue
                correo = p.get("correo") or None
                cursor.execute(
                    "SELECT id FROM docente WHERE nombre = ? AND IFNULL(email, '') = IFNULL(?, '')",
                    (p["nombre"], correo),
                )
                fila = cursor.fetchone()
                if fila:
                    docente_id = fila["id"]
                else:
                    cursor.execute(
                        "INSERT INTO docente (nombre, email) VALUES (?, ?)", (p["nombre"], correo)
                    )
                    docente_id = cursor.lastrowid
                cursor.execute(
                    "INSERT OR IGNORE INTO materia_docente (materia_id, docente_id) VALUES (?, ?)",
                    (materia_id, docente_id),
                )

        # Tareas
        tareas_moodle = []
        assignments_data = moodle.get_assignments(course_ids)
        for course in assignments_data.get("courses", []):
            materia_id = materia_id_por_moodle_id.get(course["id"])
            if not materia_id:
                continue
            for assign in course.get("assignments", []):
                due = assign.get("duedate")
                fecha_entrega = timestamp_a_iso(due) if due else None
                cursor.execute("""
                    INSERT INTO tarea (materia_id, moodle_assign_id, nombre, fecha_entrega)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(materia_id, moodle_assign_id) DO UPDATE SET
                        nombre = excluded.nombre,
                        fecha_entrega = excluded.fecha_entrega
                """, (materia_id, assign["id"], assign["name"], fecha_entrega))
                fila = cursor.execute(
                    "SELECT id FROM tarea WHERE materia_id = ? AND moodle_assign_id = ?",
                    (materia_id, assign["id"])).fetchone()
                tareas_moodle.append({"tarea_id": fila["id"], "assign_id": assign["id"],
                                      "fecha_entrega": fecha_entrega,
                                      "sin_entrega_online": bool(assign.get("nosubmissions"))})

        # Estado de entrega (mod_assign_get_submission_status). Nunca hace fallar la
        # sincronizacion: si Moodle no lo permite, las tareas quedan "sin verificar".
        entregas = sincronizar_entregas(cursor, moodle, info["userid"], estudiante_id, tareas_moodle)
        print(f"[Moodle] {entregas['detalle']}")

        # Eventos - se filtran los que ya estan registrados como tarea con el
        # mismo nombre, para no duplicar informacion en el resumen semanal
        cursor.execute("""
            SELECT t.nombre FROM tarea t
            JOIN matricula mat ON mat.materia_id = t.materia_id AND mat.estudiante_id = ?
        """, (estudiante_id,))
        nombres_tareas = {r["nombre"] for r in cursor.fetchall()}

        events = moodle.get_upcoming_events()
        cursor.execute("DELETE FROM evento WHERE estudiante_id = ?", (estudiante_id,))
        for e in events.get("events", []):
            if e["name"] in nombres_tareas:
                continue
            fecha = timestamp_a_iso(e["timestart"])
            cursor.execute("""
                INSERT INTO evento (estudiante_id, nombre, tipo, fecha)
                VALUES (?, ?, ?, ?)
            """, (estudiante_id, e["name"], e.get("eventtype", "otro"), fecha))

        # Calificaciones detalladas por componente
        grades = moodle.get_all_grades()
        if grades:
            # Se borran las de la sincronizacion anterior de ESTE estudiante
            # antes de insertar las nuevas: sin esto, cada vez que se pulsa
            # "sincronizar" las calificaciones se duplicaban. (Solo se borra
            # si llegaron datos nuevos, para no perder los viejos si Moodle
            # fallo y devolvio vacio.)
            cursor.execute("""
                DELETE FROM componente_nota
                WHERE matricula_id IN (SELECT id FROM matricula WHERE estudiante_id = ?)
            """, (estudiante_id,))
        else:
            print("[Moodle] Aviso: no llegaron calificaciones detalladas de ningun curso.")
        for g in grades:
            materia_id = _buscar_materia_id(cursor, g["curso"], estudiante_id)
            if not materia_id:
                continue
            cursor.execute("""
                SELECT id FROM matricula WHERE estudiante_id = ? AND materia_id = ?
            """, (estudiante_id, materia_id))
            mat_row = cursor.fetchone()
            if not mat_row:
                continue
            matricula_id = mat_row["id"]

            cursor.execute("""
                INSERT INTO componente_nota (matricula_id, nombre_item, calificacion, porcentaje)
                VALUES (?, ?, ?, ?)
            """, (matricula_id, g["item"], g["calificacion"], g["porcentaje"]))

        conn.commit()
        _registrar_cambios(conn, estudiante_id, "moodle")
        cursor.execute("""
            INSERT INTO sesion_extraccion (estudiante_id, plataforma, fecha, estado, detalle)
            VALUES (?, 'moodle', ?, 'exitosa', ?)
        """, (estudiante_id, ahora_iso(), f"{len(courses)} cursos; {entregas['detalle']}"))
        conn.commit()
        print(f"[Moodle] OK: {len(courses)} materias, tareas, eventos y calificaciones sincronizadas.")

    except Exception as e:
        # Una lectura que fallo a medias no debe dejar datos parciales: se conserva lo ultimo
        # que se verifico (y el detector de cambios no lo confunde con una novedad).
        conn.rollback()
        cursor.execute("""
            INSERT INTO sesion_extraccion (estudiante_id, plataforma, fecha, estado, detalle)
            VALUES (?, 'moodle', ?, 'fallida', ?)
        """, (estudiante_id, ahora_iso(), depurar(e)))
        conn.commit()
        print(f"[Moodle] Error: {depurar(e)}")

    # ---------- Parte 2: SGA (notas y asistencia oficiales) ----------
    sga = sga_client or build_sga_client()
    alumno_id = sga_alumno_id or os.getenv("SGA_ALUMNO_ID", "")

    if not sga.access_token:
        print("[SGA] Omitido: falta token de acceso del SGA")
    else:
        # Si la cuenta tiene mas de un perfil de carrera (ej. Nivelacion +
        # Grado), se cambia automaticamente al de tipo 'Grado' antes de
        # cualquier consulta - de lo contrario, endpoints como malla y
        # horario pueden devolver vacio o "acceso no permitido" si el
        # token quedo activo en el perfil equivocado. Se hace aqui (no
        # solo en el login web o en los scripts de debug) para que
        # tambien aplique al correr este script directo por consola.
        try:
            if sga.cambiar_a_perfil_regular():
                print("[SGA] Se detectaron multiples perfiles de carrera - cambiado al de tipo 'Grado'.")
        except Exception as e:
            print(f"[SGA] Aviso: no se pudo verificar/cambiar el perfil de carrera: {depurar(e)}")

        try:
            materias_sga = sga.get_materias_parseadas()
            print(f"[SGA] Materias obtenidas de la API: {len(materias_sga)}")
            actualizadas = 0
            sin_nombre = 0
            sin_match = []
            for m in materias_sga:
                if not m["nombre"]:
                    sin_nombre += 1
                    print("[SGA] Texto de la materia sin nombre (enmascarado): "
                          + _enmascarar(m.get("texto_original")))
                    continue
                materia_id = None
                if m.get("idcursomoodle"):
                    # Emparejamiento exacto por el id del curso en Moodle
                    # (mas fiable que comparar nombres).
                    cursor.execute("SELECT id FROM materia WHERE moodle_course_id = ?",
                                   (m["idcursomoodle"],))
                    fila = cursor.fetchone()
                    materia_id = fila["id"] if fila else None
                if not materia_id:
                    materia_id = _buscar_materia_id(cursor, m["nombre"], estudiante_id)
                if not materia_id:
                    sin_match.append(m["nombre"])
                    continue

                cursor.execute("""
                    UPDATE matricula SET nota_final = ?, asistencia = ?
                    WHERE estudiante_id = ? AND materia_id = ?
                """, (m["nota"], m["asistencia"], estudiante_id, materia_id))
                actualizadas += 1

            if sin_nombre:
                print(f"[SGA] Materias donde no se pudo extraer el nombre: {sin_nombre}")
                # Diagnostico: forma de una materia (solo nombres de campos y
                # tipos, nunca valores) para ver donde quedo el nombre.
                try:
                    from core.privacidad import resumir_estructura
                    primera = (sga.get_materias().get("data", {}).get("eMateriasAsignadas") or [{}])[0]
                    print("[SGA] Estructura de una materia (campos, sin valores):")
                    for linea in resumir_estructura(primera, max_items_lista=1)[:70]:
                        print("   ", linea)
                except Exception as e:
                    print(f"[SGA] No se pudo mostrar la estructura: {depurar(e)}")
            if sin_match:
                print(f"[SGA] Materias sin coincidencia en la BD: {sin_match}")

            conn.commit()
            _registrar_cambios(conn, estudiante_id, "sga")
            cursor.execute("""
                INSERT INTO sesion_extraccion (estudiante_id, plataforma, fecha, estado, detalle)
                VALUES (?, 'sga', ?, 'exitosa', ?)
            """, (estudiante_id, ahora_iso(), f"{actualizadas} materias actualizadas"))
            conn.commit()
            print(f"[SGA] OK: {actualizadas} materias actualizadas con nota/asistencia oficial.")

        except Exception as e:
            conn.rollback()                    # sin notas a medio actualizar
            cursor.execute("""
                INSERT INTO sesion_extraccion (estudiante_id, plataforma, fecha, estado, detalle)
                VALUES (?, 'sga', ?, 'fallida', ?)
            """, (estudiante_id, ahora_iso(), depurar(e)))
            conn.commit()
            print(f"[SGA] Error: {depurar(e)}")

        # --- Horario ---
        try:
            bloques = sga.get_horario_parseado()
            print(f"[SGA-horario] Bloques obtenidos de la API: {len(bloques)}")

            # Se borra el horario anterior de las materias de este estudiante
            # antes de insertar el nuevo, para evitar duplicados si se
            # vuelve a correr la sincronizacion.
            cursor.execute("""
                DELETE FROM horario WHERE materia_id IN (
                    SELECT materia_id FROM matricula WHERE estudiante_id = ?
                )
            """, (estudiante_id,))

            insertados = 0
            sin_match = []
            for b in bloques:
                if not b["materia"]:
                    continue
                materia_id = _buscar_materia_id(cursor, b["materia"], estudiante_id)
                if not materia_id:
                    sin_match.append(b["materia"])
                    continue
                cursor.execute("""
                    INSERT INTO horario (materia_id, dia, hora_inicio, hora_fin, aula)
                    VALUES (?, ?, ?, ?, ?)
                """, (materia_id, b["dia"], b["hora_inicio"], b["hora_fin"], b["aula"]))
                insertados += 1

            if sin_match:
                print(f"[SGA-horario] Materias del horario sin coincidencia en la BD: {set(sin_match)}")

            conn.commit()
            print(f"[SGA] OK: {insertados} bloques de horario sincronizados.")

        except Exception as e:
            import traceback
            print(f"[SGA-horario] Error: {depurar(e)}")
            traceback.print_exc()

        # --- Registro academico completo (historial de semestres) ---
        if not alumno_id:
            print("[SGA-registro] Omitido: falta el ID de alumno para el registro académico")
        else:
            try:
                historial = sga.get_registro_parseado(alumno_id)
                print(f"[SGA-registro] Registros obtenidos: {len(historial)}")
                if not historial:
                    # El id que se uso no sirvio: se prueban los demas
                    # identificadores disponibles (token / materias).
                    id_ok, historial = sga.buscar_registro(ya_probados=(alumno_id,))
                    if id_ok:
                        print("[SGA-registro] Se encontro un identificador que funciona.")
                        alumno_id = id_ok
                        try:
                            cursor.execute("UPDATE estudiante SET sga_alumno_id = ? WHERE id = ?",
                                           (id_ok, estudiante_id))
                        except Exception:
                            pass
                if not historial:
                    # Diagnostico: que forma tiene la respuesta (solo nombres
                    # de campos y cantidades, nunca datos personales).
                    try:
                        crudo = sga.get_registro_academico(alumno_id)
                        data = crudo.get("data", {}) if isinstance(crudo, dict) else {}
                        resumen = {k: (f"lista de {len(v)}" if isinstance(v, list) else type(v).__name__)
                                   for k, v in (data.items() if isinstance(data, dict) else [])}
                        if isinstance(crudo, dict):
                            print(f"[SGA-registro] isSuccess={crudo.get('isSuccess')} "
                                  f"message={str(crudo.get('message'))[:200]!r} "
                                  f"redirect={str(crudo.get('redirect'))[:120]!r}")
                        print(f"[SGA-registro] Diagnostico: claves de data = {resumen}; "
                              f"claves de la respuesta = {list(crudo.keys()) if isinstance(crudo, dict) else type(crudo).__name__}")
                    except Exception as e:
                        print(f"[SGA-registro] Diagnostico: no se pudo leer la respuesta cruda: {depurar(e)}")

                if historial:
                    niveles = sorted({r["nivel"] for r in historial if r.get("nivel")})
                    print(f"[SGA-registro] Niveles que trae el historial: {niveles[:15]}")

                actualizados = 0
                omitidos = 0
                for r in historial:
                    if not r["materia"]:
                        continue
                    nivel_num = nivel_a_numero(r.get("nivel"))
                    creditos = int(r["creditos"]) if r.get("creditos") is not None else None

                    # La materia puede no existir aun (si es de un semestre
                    # anterior que Moodle ya no tiene activo) - se crea si
                    # hace falta. El semestre sale del nivel de la malla.
                    materia_id = _buscar_materia_id(cursor, r["materia"], estudiante_id)
                    if not materia_id:
                        cursor.execute(
                            "INSERT INTO materia (nombre, semestre, creditos) VALUES (?, ?, ?)",
                            (r["materia"], nivel_num, creditos)
                        )
                        materia_id = cursor.lastrowid
                    else:
                        cursor.execute(
                            "UPDATE materia SET semestre = COALESCE(semestre, ?), creditos = COALESCE(creditos, ?) WHERE id = ?",
                            (nivel_num, creditos, materia_id)
                        )

                    if r["aprobada"]:
                        estado = "aprobada"
                    elif r.get("pendiente") or r["nota"] is None:
                        estado = "pendiente"
                    else:
                        estado = "reprobada"

                    # Una materia que se esta cursando ahora no debe aparecer
                    # como reprobada/pendiente por su fila en el historial.
                    if estado != "aprobada":
                        cursor.execute("""
                            SELECT 1 FROM matricula
                            WHERE estudiante_id = ? AND materia_id = ? AND estado = 'en_curso'
                        """, (estudiante_id, materia_id))
                        if cursor.fetchone():
                            omitidos += 1
                            continue

                    cursor.execute("""
                        SELECT id FROM matricula WHERE estudiante_id = ? AND materia_id = ? AND periodo IS ?
                    """, (estudiante_id, materia_id, r["periodo"]))
                    row = cursor.fetchone()
                    if not row:
                        # Si la materia ya estaba como 'pendiente' (sin periodo) por la malla,
                        # se reutiliza esa fila en vez de crear otra.
                        cursor.execute("""
                            SELECT id FROM matricula
                            WHERE estudiante_id = ? AND materia_id = ? AND periodo IS NULL AND estado = 'pendiente'
                        """, (estudiante_id, materia_id))
                        row = cursor.fetchone()

                    if row:
                        cursor.execute("""
                            UPDATE matricula SET periodo = ?, estado = ?, nota_final = ?, asistencia = ?
                            WHERE id = ?
                        """, (r["periodo"], estado, r["nota"], r["asistencia"], row["id"]))
                    else:
                        cursor.execute("""
                            INSERT INTO matricula (estudiante_id, materia_id, periodo, estado, nota_final, asistencia)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (estudiante_id, materia_id, r["periodo"], estado, r["nota"], r["asistencia"]))
                    actualizados += 1

                if omitidos:
                    print(f"[SGA-registro] {omitidos} filas del historial omitidas: son materias que cursas ahora.")
                conn.commit()
                print(f"[SGA] OK: {actualizados} registros de historial academico sincronizados.")

            except Exception as e:
                import traceback
                print(f"[SGA-registro] Error: {depurar(e)}")
                traceback.print_exc()

        # --- Malla curricular y prerrequisitos ---
        try:
            malla = sga.get_malla_parseada()
            print(f"[SGA-malla] Materias en la malla: {len(malla)}")

            # Mapa nombre normalizado -> materia_id, para resolver los
            # prerrequisitos en una segunda pasada (una vez que todas las
            # materias de la malla ya tienen su id).
            nombre_a_id = {}

            for m in malla:
                if not m["materia"]:
                    continue
                # IMPORTANTE (correccion de un bug real de aislamiento):
                # la busqueda se restringe a las materias YA vinculadas a
                # ESTE estudiante (via matricula). Antes se buscaba en
                # toda la tabla compartida, lo que podia enganchar por
                # coincidencia de nombre una materia que en realidad le
                # pertenecia a otra cuenta (ej. de una carrera distinta),
                # apareciendo incorrectamente como "pendiente" para un
                # estudiante que nunca la tuvo en su malla real.
                materia_id = _buscar_materia_id(cursor, m["materia"], estudiante_id)
                if not materia_id:
                    cursor.execute(
                        "INSERT INTO materia (nombre, semestre, creditos) VALUES (?, ?, ?)",
                        (m["materia"], m["nivel_orden"], m["creditos"])
                    )
                    materia_id = cursor.lastrowid
                else:
                    cursor.execute(
                        "UPDATE materia SET semestre = ?, creditos = ? WHERE id = ?",
                        (m["nivel_orden"], m["creditos"], materia_id)
                    )
                nombre_a_id[_normalizar(m["materia"])] = materia_id

                # Se vincula la materia a ESTE estudiante como 'pendiente'
                # si todavia no existe ninguna matricula suya para ella
                # (evita perder el vinculo estudiante-materia para las
                # materias que aun no ha cursado).
                cursor.execute(
                    "SELECT id FROM matricula WHERE estudiante_id = ? AND materia_id = ?",
                    (estudiante_id, materia_id)
                )
                if not cursor.fetchone():
                    cursor.execute(
                        "INSERT INTO matricula (estudiante_id, materia_id, estado) VALUES (?, ?, 'pendiente')",
                        (estudiante_id, materia_id)
                    )

            # Segunda pasada: prerrequisitos (ya con todos los ids resueltos)
            prereq_insertados = 0
            for m in malla:
                if not m["materia"]:
                    continue
                materia_id = nombre_a_id.get(_normalizar(m["materia"]))
                for prereq_nombre in m["prerrequisitos"]:
                    prereq_id = nombre_a_id.get(_normalizar(prereq_nombre)) or _buscar_materia_id(cursor, prereq_nombre, estudiante_id)
                    if prereq_id and materia_id:
                        cursor.execute(
                            "INSERT OR IGNORE INTO prerrequisito (materia_id, materia_requisito_id) VALUES (?, ?)",
                            (materia_id, prereq_id)
                        )
                        prereq_insertados += 1

            conn.commit()
            print(f"[SGA] OK: malla sincronizada ({len(malla)} materias, {prereq_insertados} prerrequisitos).")

        except Exception as e:
            import traceback
            print(f"[SGA-malla] Error: {depurar(e)}")
            traceback.print_exc()

    conn.close()


if __name__ == "__main__":
    sync()

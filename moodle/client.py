"""
Cliente para la API de Moodle Web Services (Aulas Virtuales / Pregrado Virtual).

A diferencia del SGA, aqui SI podemos automatizar el login completo, porque
confirmamos en las pruebas que login/token.php no tiene deteccion de bots.
"""
import os
import re
import sys
import base64
import requests
from urllib.parse import unquote

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.privacidad import depurar  # noqa: E402


def extraer_token_moodle(texto: str) -> str:
    """
    Autenticacion por token (sin contraseñas): el estudiante copia su token
    de sesion de Moodle y lo pega en el prototipo. Esta funcion acepta
    cualquiera de estos formatos y devuelve el token listo para usar:

    1. El enlace completo del lanzador movil: 'moodlemobile://token=BASE64'
    2. Solo el codigo BASE64 que va despues de 'token='
    3. El token crudo (32 caracteres hexadecimales)

    El BASE64 de Moodle decodifica a 'siteid:::token' o
    'siteid:::token:::privatetoken'; el token que nos sirve es la 2da parte.
    """
    texto = (texto or "").strip().strip("\"'")
    if not texto:
        return ""
    if "token=" in texto:
        texto = texto.split("token=", 1)[1]
    texto = unquote(texto).strip()

    # Token crudo de Moodle: 32 caracteres hexadecimales
    if re.fullmatch(r"[0-9a-fA-F]{32}", texto):
        return texto

    # Formato del enlace movil: base64("siteid:::token[:::privatetoken]")
    try:
        relleno = "=" * (-len(texto) % 4)
        decodificado = base64.b64decode(texto + relleno).decode("utf-8")
        partes = decodificado.split(":::")
        if len(partes) >= 2 and partes[1].strip():
            return partes[1].strip()
    except Exception:
        pass
    return texto


def limpiar_nombre_curso(nombre_completo: str) -> str:
    """
    Moodle devuelve nombres como:
    'SISTEMA DE AUTOMATIZACIÓN INDUSTRIAL - [IS08SA] - A1 - SOFTWARE2019'
    Esta funcion se queda solo con la parte antes del primer ' - '.
    """
    return nombre_completo.split(" - ")[0].strip()


# Etiquetas de componente de evaluacion reconocidas (patron institucional,
# confirmado tanto en el SGA como en los nombres de tareas de Moodle).
COMPONENTES_VALIDOS = ["N1", "N2", "N3", "N4", "EXP1", "EXP2", "EX", "EXT", "RE"]


def extraer_componente(nombre_tarea: str):
    """
    Busca una etiqueta de componente entre parentesis dentro del nombre de
    una tarea de Moodle, ej: 'S3-TAREA_1. (N2) Analizar un residuo...'
    devuelve 'N2'. Si no encuentra ninguna, devuelve None.
    """
    if not nombre_tarea:
        return None
    match = re.search(r"\((" + "|".join(COMPONENTES_VALIDOS) + r")\)", nombre_tarea, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


class MoodleClient:
    def __init__(self, base_url: str, token: str = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.session = requests.Session()

    def login(self, username: str, password: str, service: str = "moodle_mobile_app"):
        """Obtiene un token nuevo. Lo guarda en self.token y lo retorna."""
        url = f"{self.base_url}/login/token.php"
        try:
            resp = self.session.get(url, params={
                "username": username,
                "password": password,
                "service": service,
            })
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            # El mensaje de requests incluye la URL completa (con usuario y
            # contrasena): se depura y se corta la cadena de excepciones.
            raise RuntimeError(f"Error de red al iniciar sesion en Moodle: {depurar(e)}") from None

        if "error" in data:
            raise ValueError(f"Error de Moodle al hacer login: {data['error']}")

        self.token = data["token"]
        return self.token

    def call(self, wsfunction: str, **params):
        """Llama a cualquier funcion de la API REST de Moodle."""
        if not self.token:
            raise PermissionError("No hay token. Llama a login() primero.")

        url = f"{self.base_url}/webservice/rest/server.php"
        payload = {
            "wstoken": self.token,
            "wsfunction": wsfunction,
            "moodlewsrestformat": "json",
            **params,
        }
        try:
            resp = self.session.get(url, params=payload)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            # requests incluye la URL completa en el mensaje, y la URL lleva el
            # wstoken: se depura y se corta la cadena para que el token no
            # llegue a los registros, a sesion_extraccion ni a la pantalla.
            raise RuntimeError(f"Error de red al llamar a Moodle ({wsfunction}): {depurar(e)}") from None

        if isinstance(data, dict) and data.get("exception"):
            raise RuntimeError(f"Error Moodle ({wsfunction}): {data.get('message')}")

        return data

    # --- Métodos de conveniencia para las funciones que ya confirmamos que existen ---

    def get_site_info(self):
        return self.call("core_webservice_get_site_info")

    def get_courses(self):
        """Cursos matriculados del usuario actual."""
        site_info = self.get_site_info()
        userid = site_info["userid"]
        return self.call("core_enrol_get_users_courses", userid=userid)

    def get_upcoming_events(self):
        return self.call("core_calendar_get_calendar_upcoming_view")

    def get_assignments(self, course_ids: list = None):
        params = {}
        if course_ids:
            for i, cid in enumerate(course_ids):
                params[f"courseids[{i}]"] = cid
        return self.call("mod_assign_get_assignments", **params)

    def get_submission_status(self, assign_id: int, userid: int):
        """Estado de entrega de una tarea para un usuario (Moodle >= 3.1). Puede no estar
        habilitada en el servicio del sitio: quien llama debe tolerar el error."""
        return self.call("mod_assign_get_submission_status", assignid=assign_id, userid=userid)

    def get_grades(self, course_id: int, userid: int):
        return self.call("gradereport_user_get_grade_items", courseid=course_id, userid=userid)

    def get_teachers(self, course_id: int):
        """
        Trae los profesores matriculados en un curso, con nombre y correo
        (el correo puede venir vacio si la configuracion de privacidad de
        Moodle no lo expone a otros usuarios - no todos los sitios lo permiten).
        """
        try:
            usuarios = self.call("core_enrol_get_enrolled_users", courseid=course_id)
        except Exception:
            return []

        profesores = []
        for u in usuarios:
            roles = [r.get("shortname", "") for r in u.get("roles", [])]
            if any("teacher" in r for r in roles):
                profesores.append({
                    "nombre": u.get("fullname"),
                    "correo": u.get("email") or None,
                })
        return profesores

    @staticmethod
    def _limpiar_html(valor) -> str:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(valor or ""))).replace("&nbsp;", " ").strip()

    def get_grades_table_rows(self, course_id, userid, nombre_curso):
        """
        Lee gradereport_user_get_grades_table y devuelve filas
        {curso, item, calificacion, porcentaje}. Las filas sin nota son
        encabezados de categoria (N1, N2, EXP1...): se usan para nombrar
        cada tarea ('N2 - S3-TALLER_1') y los totales ('Total N2').
        """
        tabla = self.call("gradereport_user_get_grades_table", courseid=course_id, userid=userid)
        filas = (tabla.get("tables") or [{}])[0].get("tabledata", [])
        resultado = []
        categoria = None
        for fila in filas:
            if not isinstance(fila, dict):
                continue
            nombre = self._limpiar_html((fila.get("itemname") or {}).get("content"))
            if not nombre:
                continue
            if "grade" not in fila:
                # Encabezado: curso o categoria
                categoria = nombre if len(nombre) <= 12 else None
                continue
            nota = self._limpiar_html(fila["grade"].get("content"))
            nota = re.sub(r"\s*Acciones.*$", "", nota).strip() or "-"
            pct = self._limpiar_html((fila.get("percentage") or {}).get("content"))

            es_total = nombre.startswith("Cálculo total")
            nombre = re.sub(
                r"^(Cálculo total|Tarea|Cuestionario|Foro|Taller|Lección|Laboratorio)\s+", "", nombre
            ).strip()
            if not es_total and categoria:
                nombre = f"{categoria} - {nombre}"
            resultado.append({
                "curso": nombre_curso,
                "item": nombre,
                "calificacion": nota,
                "porcentaje": pct if pct != "-" else "",
            })
            if es_total:
                categoria = None
        return resultado

    def get_all_grades(self):
        """
        Trae calificaciones detalladas de TODOS los cursos matriculados,
        organizadas por COMPONENTE de evaluacion (N1, N2, N3, N4, EXP1,
        EXP2, EX, EXT, RE...).

        Detalle tecnico importante (confirmado inspeccionando la API real):
        las filas de tipo 'category' (el TOTAL de una categoria como N1)
        vienen con itemname vacio - Moodle no expone el nombre legible ahi.
        En cambio, la tarea individual dentro de esa categoria SI trae la
        etiqueta incrustada en su propio nombre, ej:
        "S3-TAREA_1. (N2) Analizar un residuo..."

        Estrategia: se extrae la etiqueta (N1, N2, EXP1...) del nombre de
        cada tarea via expresion regular, se usa su 'categoryid' para
        encontrar la fila de categoria correspondiente (que comparte ese
        mismo id como su propio 'id'), y se toma la calificacion de esa
        fila de categoria (el total ya calculado), no la de la tarea
        individual sola.
        """
        info = self.get_site_info()
        userid = info["userid"]
        courses = self.get_courses()

        resultado = []
        for c in courses:
            try:
                nombre_curso = limpiar_nombre_curso(c["fullname"])

                # Via preferida: la tabla del "Informe del usuario", que SI
                # trae los nombres reales (N1, Total N2, ...). Si no esta
                # disponible o viene vacia, se usa el metodo de abajo.
                try:
                    filas_tabla = self.get_grades_table_rows(c["id"], userid, nombre_curso)
                except Exception:
                    filas_tabla = []
                if filas_tabla:
                    resultado.extend(filas_tabla)
                    continue

                data = self.get_grades(c["id"], userid)

                for grade_item in data.get("usergrades", []):
                    items = grade_item.get("gradeitems", [])

                    # Mapa: id de categoria -> fila de categoria (total)
                    categorias_por_id = {
                        item["id"]: item for item in items
                        if item.get("itemtype") == "category"
                    }

                    etiquetas_ya_vistas = set()
                    for item in items:
                        if item.get("itemtype") != "mod":
                            continue
                        etiqueta = extraer_componente(item.get("itemname") or "")
                        if not etiqueta:
                            # Respaldo: hay cursos cuyas tareas NO traen la
                            # etiqueta (N1), (N2)... en el nombre (ej.
                            # "S3-TALLER_1", "S5-TAREA_1"). Antes esas tareas
                            # se descartaban en silencio y el asistente decia
                            # "no tengo calificaciones". Ahora se reporta la
                            # tarea individual con su propia nota. (El total
                            # por categoria no se puede nombrar en estos
                            # cursos: la API de Moodle devuelve las
                            # categorias sin nombre.)
                            nombre_item = (item.get("itemname") or "").strip()
                            if nombre_item:
                                resultado.append({
                                    "curso": nombre_curso,
                                    "item": nombre_item,
                                    "calificacion": (item.get("gradeformatted") or "").strip(),
                                    "porcentaje": (item.get("percentageformatted") or "").strip(),
                                })
                            continue

                        cat_id = item.get("categoryid")
                        fila_categoria = categorias_por_id.get(cat_id)

                        # Evita duplicar la misma categoria si tuviera mas
                        # de una tarea adentro (se reporta una sola vez).
                        clave = (cat_id, etiqueta)
                        if clave in etiquetas_ya_vistas:
                            continue
                        etiquetas_ya_vistas.add(clave)

                        if fila_categoria:
                            calificacion = (fila_categoria.get("gradeformatted") or "").strip()
                            porcentaje = (fila_categoria.get("percentageformatted") or "").strip()
                        else:
                            # Si no se encuentra la categoria, se usa la
                            # nota de la tarea individual como respaldo.
                            calificacion = (item.get("gradeformatted") or "").strip()
                            porcentaje = (item.get("percentageformatted") or "").strip()

                        resultado.append({
                            "curso": nombre_curso,
                            "item": etiqueta,
                            "calificacion": calificacion,
                            "porcentaje": porcentaje,
                        })
            except Exception as e:
                # Si un curso en particular falla (ej. sin calificaciones aun),
                # se continua con los demas en vez de romper todo el reporte.
                # Pero ya NO en silencio: se avisa en la consola del servidor,
                # para poder distinguir "no hay notas" de "fallo la consulta".
                print(f"[Moodle] Aviso: no se pudieron leer las notas de "
                      f"'{c.get('fullname', c.get('id'))}': {e}")
                continue
        return resultado


def build_client_from_env() -> MoodleClient:
    """Cliente de Moodle para uso por consola. Si MOODLE_TOKEN esta definido se
    usa ese token (recomendado, sin contrasena)."""
    return MoodleClient(
        base_url=os.getenv("MOODLE_BASE_URL", "https://aulagradoa.unemi.edu.ec"),
        token=os.getenv("MOODLE_TOKEN") or None,
    )


if __name__ == "__main__":
    # Prueba de conexion: solo imprime CANTIDADES, nunca nombres ni notas.
    from dotenv import load_dotenv
    load_dotenv()

    client = build_client_from_env()
    if not client.token:
        print("Falta MOODLE_TOKEN en .env (el token del enlace moodlemobile://token=...).")
    else:
        try:
            info = client.get_site_info()
            print(f"Conexion exitosa (userid={info['userid']}).")
            print(f"Cursos matriculados: {len(client.get_courses())}")
            print(f"Proximos eventos: {len(client.get_upcoming_events().get('events', []))}")
        except Exception as e:
            print(f"Error: {depurar(e)}")

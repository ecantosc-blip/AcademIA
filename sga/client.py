"""
Cliente para la API del ERP academico institucional (backend real usado como
fuente de datos del prototipo; hoy conecta contra el SGA de UNEMI, pero la
integracion esta pensada para funcionar contra cualquier ERP academico con
un esquema de endpoints similar).

Estrategia de autenticación:
El login del SGA tiene deteccion de bots (getBotSignals, fingerprinting de
navegador), por lo que NO lo automatizamos. En su lugar:

1. El usuario inicia sesion UNA VEZ en su propio navegador normal.
2. Copia el "access" y "refresh" token desde DevTools > Network > login.json > Response.
3. Los pega en el archivo .env (ACCESS_TOKEN, REFRESH_TOKEN).
4. Este cliente usa el access_token para las consultas, y cuando expira,
   usa el refresh_token para pedir uno nuevo automaticamente.
"""
import os
import re
import time
import json
import base64
import requests
from datetime import datetime


def decodificar_jwt(token: str) -> dict:
    """
    Decodifica el 'payload' de un JWT SIN verificar la firma (no tenemos
    la clave secreta del servidor, y no hace falta para solo LEER los
    datos que ya nos pertenecen - es el mismo principio que usa cualquier
    frontend para leer un token propio).
    """
    partes = token.split(".")
    if len(partes) != 3:
        raise ValueError("Token con formato invalido")
    payload_b64 = partes[1]
    # Base64 URL-safe necesita padding correcto
    padding = "=" * (-len(payload_b64) % 4)
    payload_json = base64.urlsafe_b64decode(payload_b64 + padding)
    return json.loads(payload_json)


def segundos_para_vencer(token: str):
    """
    Cuantos segundos le quedan al token del ERP antes de vencer, leyendo el
    campo 'exp' (fecha de vencimiento) que ya viene dentro del propio token.
    No hace ninguna peticion de red.

    Devuelve un numero (negativo si YA vencio), o None si el texto no es un
    token legible o no trae fecha de vencimiento.
    """
    try:
        exp = decodificar_jwt(token.strip()).get("exp")
        return float(exp) - time.time() if exp is not None else None
    except Exception:
        return None


RUTA_REFRESH = "/api/1.0/jwt/token/refresh"

MENSAJE_TOKEN_VENCIDO = (
    "Token expirado y no se pudo renovar. Vuelve a iniciar sesion en el ERP "
    "desde tu navegador, copia un token nuevo y pegalo en el login de la web "
    "(o en SGA_ACCESS_TOKEN / SGA_REFRESH_TOKEN del .env si usas la consola)."
)


def extraer_nota_asistencia(texto: str):
    """
    Busca un patron como '[Nota:0.0 Asis:100.0]' dentro del texto largo
    que devuelve el SGA y extrae los dos numeros.
    Devuelve (nota, asistencia) como floats, o (None, None) si no encuentra.
    """
    match = re.search(r"\[Nota:([\d.]+)\s+Asis:([\d.]+)\]", texto)
    if match:
        return float(match.group(1)), float(match.group(2))
    return None, None


def extraer_nombre_materia(texto: str):
    """
    El texto viene como:
    'CANTOS CHEVEZ ERICK ISAAC - 0928479005, SOFTWARE 2019  NOCTURNO - FACI -
     FACULTAD CIENCIAS E INGENIERÍA  DISEÑO DE INTEGRACIÓN CURRICULAR - [FCI08IC] - ...'

    El nombre de la materia esta despues del nombre de la facultad/carrera y
    antes del codigo entre corchetes tipo [FCI08IC].

    CASO ESPECIAL detectado con cuentas de nivelacion/admision: el texto
    trae informacion adicional del grupo de admision con MAS guiones antes
    del nombre real de la materia, por ejemplo:
    '...INGENIERÍA EN ALIMENTOS  FDS ADMISION - ADMI - ADMISION  PROPEDÉUTICO - [ADMI001]'
    En ese caso el regex captura todo el tramo completo ('EN ALIMENTOS FDS
    ADMISION - ADMI - ADMISION  PROPEDÉUTICO'), pero el nombre real de la
    materia es solo la ULTIMA parte despues del ultimo ' - '. Por eso, si
    el texto capturado tiene guiones internos, nos quedamos solo con lo
    que sigue despues del ultimo uno.
    """
    match = re.search(r"INGENIER[ÍI]A\s+(.+?)\s+-\s+\[", texto, re.IGNORECASE)
    if not match:
        # Plan B: no depender de la palabra INGENIERIA. Se toma lo que hay
        # justo antes de " - [CODIGO]" y se queda con el ultimo tramo
        # separado por doble espacio (el nombre de la materia).
        alt = re.search(r"([^\[\]]+?)\s+-\s+\[[A-Z0-9]+\]", texto or "")
        if not alt:
            return None
        tramo = alt.group(1).strip()
        if "  " in tramo:
            tramo = tramo.split("  ")[-1].strip()
        if " - " in tramo:
            tramo = tramo.split(" - ")[-1].strip()
        return tramo or None

    capturado = match.group(1).strip()
    if " - " in capturado:
        capturado = capturado.split(" - ")[-1].strip()
    # Segundo nivel de limpieza: en cuentas de nivelacion/admision, incluso
    # despues de quitar los guiones queda un prefijo tipo "ADMISION" pegado
    # al nombre real con doble espacio (ej. "ADMISION  PROPEDÉUTICO").
    # El separador real entre "grupo" y "materia" en el texto que devuelve el ERP
    # parece ser doble espacio - nos quedamos con el ultimo segmento.
    if "  " in capturado:
        capturado = capturado.split("  ")[-1].strip()
    return capturado


class SGAClient:
    def __init__(self, base_url: str, access_token: str = None, refresh_token: str = None):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.session = requests.Session()

    def _headers(self, con_referer: str = None):
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Origin": "https://sgaestudiante.unemi.edu.ec",
        }
        if con_referer:
            headers["Referer"] = con_referer
        return headers

    def refresh_access_token(self) -> bool:
        """
        Renueva el access_token con el refresh_token. Endpoint confirmado en
        DevTools (el mismo que usa el navegador cuando el token vence):
            POST {base}/api/1.0/jwt/token/refresh   cuerpo JSON {"refresh": "..."}
            respuesta: {"access": "..."} (y a veces un "refresh" nuevo)
        Devuelve True si renovo. Si el refresh tambien vencio o es invalido,
        devuelve False y el estudiante debe entrar con un token nuevo.
        """
        if not self.refresh_token:
            return False
        url = f"{self.base_url}{RUTA_REFRESH}"
        resp = None
        # El navegador manda tambien el Authorization (aunque ya este vencido);
        # si el servidor lo rechaza por eso, se reintenta sin esa cabecera.
        for con_auth in (True, False):
            headers = {"Accept": "application/json", "Origin": "https://sgaestudiante.unemi.edu.ec"}
            if con_auth and self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"
            try:
                resp = self.session.post(url, headers=headers, json={"refresh": self.refresh_token}, timeout=20)
            except requests.RequestException:
                return False
            if resp.status_code == 200:
                break
        else:
            return False
        try:
            data = resp.json()
        except ValueError:
            return False
        if not isinstance(data, dict):
            return False
        interno = data.get("data") if isinstance(data.get("data"), dict) else {}
        nuevo_access = data.get("access") or interno.get("access")
        if not nuevo_access:
            return False
        self.access_token = nuevo_access
        nuevo_refresh = data.get("refresh") or interno.get("refresh")
        if nuevo_refresh:           # si el servidor rota el refresh, se guarda el nuevo
            self.refresh_token = nuevo_refresh
        return True

    def _asegurar_token(self):
        """Renueva por adelantado si al access_token le queda menos de 1 minuto."""
        if self.refresh_token:
            resto = segundos_para_vencer(self.access_token or "")
            if resto is not None and resto < 60:
                self.refresh_access_token()

    def _get(self, path: str, params: dict = None, referer: str = None):
        self._asegurar_token()
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, headers=self._headers(referer), params=params)

        if resp.status_code == 401:
            # Token expirado - intentar renovar y reintentar una vez
            if self.refresh_access_token():
                resp = self.session.get(url, headers=self._headers(referer), params=params)
            else:
                raise PermissionError(MENSAJE_TOKEN_VENCIDO)

        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, params: dict = None, referer: str = None, json_body: dict = None):
        self._asegurar_token()
        url = f"{self.base_url}{path}"
        resp = self.session.post(url, headers=self._headers(referer), params=params, json=json_body)

        if resp.status_code == 401:
            if self.refresh_access_token():
                resp = self.session.post(url, headers=self._headers(referer), params=params, json=json_body)
            else:
                raise PermissionError(MENSAJE_TOKEN_VENCIDO)

        resp.raise_for_status()
        return resp.json()

    def get_perfiles(self):
        """
        Lee, del propio access_token actual, la lista de 'perfiles' de
        carrera que tiene la cuenta (ej. una cuenta puede tener a la vez
        un perfil de 'Nivelación' y uno de 'Grado' en distinta carrera).
        Cada perfil trae: id, carrera (nombre), clasificacion (1=Grado,
        3=Nivelacion, segun lo observado), display_clasificacion.
        """
        payload = decodificar_jwt(self.access_token)
        return payload.get("perfiles", [])

    def get_perfil_principal_id(self):
        """ID del perfil actualmente activo en el token."""
        payload = decodificar_jwt(self.access_token)
        return payload.get("perfilprincipal", {}).get("id")

    def cambiar_a_perfil_regular(self):
        """
        Si la cuenta tiene mas de un perfil de carrera (ej. Nivelacion +
        Grado), cambia automaticamente al de clasificacion 1 ('Grado' -
        carrera regular), que es el que normalmente tiene datos completos
        de horario/notas/registro academico. Si la cuenta solo tiene un
        perfil, o el perfil de grado ya esta activo, no hace nada.

        Devuelve True si se cambio de perfil (y por lo tanto access_token
        y refresh_token se actualizaron), False si no hizo falta cambiar.
        """
        try:
            perfiles = self.get_perfiles()
            principal_id = self.get_perfil_principal_id()
        except Exception:
            return False

        # Se busca un perfil de clasificacion 1 ('Grado') que no sea ya
        # el activo.
        perfil_grado = next(
            (p for p in perfiles if p.get("clasificacion") == 1 and p.get("id") != principal_id),
            None
        )
        if not perfil_grado:
            return False

        url = f"{self.base_url}/api/1.0/jwt/token/change/career"
        resp = self.session.post(
            url,
            headers=self._headers(),
            json={"perfil_id": perfil_grado["id"], "refresh": self.refresh_token},
        )
        resp.raise_for_status()
        data = resp.json()

        nuevo_access = data.get("access") or data.get("access_token")
        nuevo_refresh = data.get("refresh") or data.get("refresh_token")
        if not nuevo_access:
            return False

        self.access_token = nuevo_access
        if nuevo_refresh:
            self.refresh_token = nuevo_refresh
        return True

    def get_materias(self):
        """
        Trae TODAS las materias del alumno autenticado, con sus notas,
        asistencia y estado. No necesita ID de alumno - el servidor lo
        identifica por el token.
        """
        return self._get("/api/1.0/jwt/alumno/materias")

    def get_malla(self):
        """
        Trae la malla curricular del alumno (plan de estudios, y
        probablemente estado de aprobacion/prerrequisitos por materia).
        Endpoint confirmado: GET /api/1.0/jwt/alumno/malla

        Se agrega el parametro 'time' (timestamp) como rompe-cache: se
        detecto que el servidor del ERP puede devolver una respuesta de
        error cacheada de un intento anterior (ej. con un token vencido),
        incluso despues de que el token ya es valido, si la cache esta
        indexada solo por URL y no por el token de autorizacion.
        """
        return self._get("/api/1.0/jwt/alumno/malla",
                          params={"time": int(time.time() * 1000)},
                          referer="https://sgaestudiante.unemi.edu.ec/alu_malla")

    def get_malla_parseada(self):
        """
        Version simplificada de get_malla(): aplana data.eAsignaturasMalla
        en una lista de materias del plan de estudios, cada una con su
        nivel (semestre), creditos, y prerrequisitos (materias que hay
        que aprobar antes).

        IMPORTANTE: este endpoint describe la ESTRUCTURA del plan de
        estudios (que materias existen y en que orden), no el avance del
        estudiante. Para saber cuales ya aprobo, hay que cruzar esta lista
        con get_registro_parseado() (que si tiene el estado aprobada/
        pendiente por materia).
        """
        raw = self.get_malla()
        asignaturas = raw.get("data", {}).get("eAsignaturasMalla", [])

        resultado = []
        for item in asignaturas:
            asignatura = item.get("asignatura", {}) or {}
            nivelmalla = item.get("nivelmalla", {}) or {}

            prerrequisitos = []
            for pred in item.get("predecesoras", []) or []:
                try:
                    nombre_prereq = pred.get("predecesora", {}).get("asignatura", {}).get("nombre")
                    if nombre_prereq:
                        prerrequisitos.append(nombre_prereq)
                except AttributeError:
                    continue

            resultado.append({
                "materia": asignatura.get("nombre"),
                "codigo": asignatura.get("codigo"),
                "creditos": asignatura.get("creditos"),
                "nivel": nivelmalla.get("nombre"),
                "nivel_orden": nivelmalla.get("orden"),
                "es_nivelacion": nivelmalla.get("nivelacion", False),
                "prerrequisitos": prerrequisitos,
            })
        return resultado

    def get_alumno_id_automatico(self):
        """
        Extrae automaticamente el ID de alumno necesario para el endpoint
        de Registro Academico (/notas/{id}/), sin que el usuario tenga que
        buscarlo manualmente en DevTools.

        Se encontro que este ID esta anidado dentro de la respuesta de
        materias(): data.eMateriasAsignadas[0].matricula.inscripcion.id
        (confirmado comparando con el ID real visto en la URL del
        navegador para "Registro Academico" - coinciden exactamente).

        Devuelve None si no se puede extraer (ej. estructura distinta
        para cuentas de nivelacion/admision, o el alumno no tiene materias
        asignadas todavia).
        """
        # Fuente actual: el propio token trae la inscripcion (confirmado con
        # cuenta real: es el id que acepta /notas/{id}/).
        try:
            id_token = (decodificar_jwt(self.access_token).get("inscripcion") or {}).get("id")
            if id_token:
                return id_token
        except Exception:
            pass
        # Respaldo (formato anterior de la respuesta de materias)
        try:
            raw = self.get_materias()
            materias = raw.get("data", {}).get("eMateriasAsignadas", [])
            if not materias:
                return None
            inscripcion = (materias[0].get("matricula") or {}).get("inscripcion") or {}
            return inscripcion.get("id")
        except Exception:
            return None

    def get_registro_academico(self, alumno_id: str):
        """
        Trae el 'Registro Academico' completo del alumno (historial de
        semestres, no solo el actual). A diferencia de get_materias(),
        este endpoint SI necesita el ID del alumno en la URL - se obtiene
        de la URL que se ve en DevTools al entrar a esa seccion del SGA.
        """
        return self._get(f"/api/1.0/jwt/alumno/notas/{alumno_id}/",
                          referer="https://sgaestudiante.unemi.edu.ec/alu_notas")

    @staticmethod
    def _parsear_registro(raw):
        """Aplana data.eRecordaAademicos en una lista simple de materias."""
        registros = (raw.get("data", {}) or {}).get("eRecordaAademicos", []) or []

        resultado = []
        for r in registros:
            asignatura = r.get("asignatura", {}) or {}
            malla = r.get("asignaturamalla", {}) or {}
            nivel = (malla.get("nivelmalla") or {}).get("nombre")
            # Formato anterior: el periodo venia en materiaregular.nivel.periodo.
            periodo_viejo = (((r.get("materiaregular") or {}).get("nivel") or {}).get("periodo") or {}).get("nombre")
            fecha = r.get("fecha")

            resultado.append({
                "materia": asignatura.get("nombre"),
                "nota": r.get("nota"),
                "asistencia": r.get("asistencia"),
                "aprobada": r.get("aprobada"),
                "pendiente": r.get("pendiente"),
                "creditos": r.get("creditos") if r.get("creditos") is not None else malla.get("creditos"),
                # El ERP actual ya no entrega el periodo: se usa la fecha del registro.
                "periodo": periodo_viejo or (str(fecha)[:10] if fecha else None),
                "fecha": fecha,
                "nivel": nivel,
                "intentos": r.get("matriculas"),
            })
        return resultado

    def get_registro_parseado(self, alumno_id: str):
        """
        Version simplificada de get_registro_academico(): aplana la
        estructura anidada (data.eRecordaAademicos) en una lista simple
        de materias cursadas en CUALQUIER semestre (no solo el actual),
        con su estado de aprobacion.
        """
        return self._parsear_registro(self.get_registro_academico(alumno_id))

    def candidatos_alumno_id(self):
        """
        Lista de (etiqueta, valor) con los identificadores que podrian servir
        en /notas/{id}/. El ERP ahora codifica los ids y no es seguro cual
        espera ese endpoint, asi que se juntan los que hay en el token y en
        la respuesta de materias, sin repetir valores.
        """
        def en(d, *ruta):
            for k in ruta:
                d = d.get(k) if isinstance(d, dict) else None
            return d

        candidatos = []
        try:
            p = decodificar_jwt(self.access_token)
            for et, ruta in (("token.inscripcion.id", ("inscripcion", "id")),
                             ("token.matricula.id", ("matricula", "id")),
                             ("token.persona.id", ("persona", "id"))):
                candidatos.append((et, en(p, *ruta)))
        except Exception:
            pass
        try:
            materias = self.get_materias().get("data", {}).get("eMateriasAsignadas", []) or []
            if materias:
                for et, ruta in (("materia.matricula.inscripcion.id", ("matricula", "inscripcion", "id")),
                                 ("materia.matricula.id", ("matricula", "id")),
                                 ("materia.inscripcion.id", ("inscripcion", "id"))):
                    candidatos.append((et, en(materias[0], *ruta)))
        except Exception:
            pass

        vistos, resultado = set(), []
        for et, v in candidatos:
            if v and v not in vistos:
                vistos.add(v)
                resultado.append((et, v))
        return resultado

    def buscar_registro(self, ya_probados=()):
        """
        Prueba cada identificador candidato contra el historial y devuelve
        (id_que_funciono, lista_parseada), o (None, []). Imprime solo la
        etiqueta del candidato y el resultado, nunca el valor del id.
        """
        for etiqueta, valor in self.candidatos_alumno_id():
            if valor in ya_probados:
                continue
            try:
                raw = self.get_registro_academico(valor)
            except Exception as e:
                print(f"[SGA-registro] candidato {etiqueta}: error {str(e)[:80]}")
                continue
            parseado = self._parsear_registro(raw) if isinstance(raw, dict) else []
            print(f"[SGA-registro] candidato {etiqueta}: isSuccess={raw.get('isSuccess') if isinstance(raw, dict) else None} "
                  f"registros={len(parseado)}")
            if parseado:
                return valor, parseado
        return None, []

    def get_horario(self):
        """
        Trae el horario de clases del alumno autenticado.
        Endpoint confirmado via DevTools: POST /api/1.0/jwt/alumno/horario
        Requiere: query param 'time' (timestamp) + body JSON {"action": "loadInit"}
        (confirmado inspeccionando la pestaña Payload de la peticion real
        del navegador - sin el campo "action" el servidor responde con
        error "Parametro de accion no encontrado").
        """
        return self._post(
            "/api/1.0/jwt/alumno/horario",
            params={"time": int(time.time() * 1000)},
            referer="https://sgaestudiante.unemi.edu.ec/alu_horario",
            json_body={"action": "loadInit"},
        )

    def get_horario_parseado(self):
        """
        Version simplificada de get_horario(): aplana la estructura anidada
        (turnos -> dias -> clases) en una lista simple de bloques de clase.
        Solo usa 'aSemanaTurnoActuales' (horario del periodo actual), ignora
        'aSemanaTurnoPasadas'.
        """
        raw = self.get_horario()
        data = raw.get("data", {})
        turnos = data.get("aTurnos", [])

        dias_nombre = {1: "Lunes", 2: "Martes", 3: "Miércoles", 4: "Jueves",
                       5: "Viernes", 6: "Sábado", 7: "Domingo"}

        bloques = []
        for turno in turnos:
            hora_inicio = turno.get("comienza")
            hora_fin = turno.get("termina")
            for dia_info in turno.get("aSemanaTurnoActuales", []):
                dia_num = dia_info.get("dia")
                for clase in dia_info.get("aClases", []):
                    bloques.append({
                        "materia": clase.get("asignatura"),
                        "dia": dias_nombre.get(dia_num, str(dia_num)),
                        "hora_inicio": hora_inicio,
                        "hora_fin": hora_fin,
                        "aula": clase.get("aula"),
                        "profesor": clase.get("profesor"),
                    })
        return bloques

    def get_materias_parseadas(self):
        """
        Version simplificada y limpia de get_materias(): en vez del JSON
        crudo (muy anidado), devuelve una lista de diccionarios simples,
        uno por materia, con solo los datos que el asistente necesita.
        """
        raw = self.get_materias()
        materias_raw = raw.get("data", {}).get("eMateriasAsignadas", [])

        resultado = []
        for m in materias_raw:
            nombre_completo = m.get("display", "") or ""
            materia = m.get("materia", {}) or {}

            # Estructura actual del ERP: el nombre, la nota y la asistencia
            # vienen en campos directos. Si no estan (cuentas/versiones
            # anteriores), se cae al texto largo 'display' como antes.
            nombre_materia = ((materia.get("asignatura") or {}).get("nombre")
                              or extraer_nombre_materia(nombre_completo))
            nota, asistencia = self._a_float(m.get("notafinal")), self._a_float(m.get("asistenciafinal"))
            if nota is None and asistencia is None:
                nota, asistencia = extraer_nota_asistencia(nombre_completo)

            resultado.append({
                "id": m.get("id"),
                "nombre": nombre_materia,
                "nota": nota,
                "asistencia": asistencia,
                "idcursomoodle": materia.get("idcursomoodle"),
                "texto_original": nombre_completo,
            })
        return resultado

    @staticmethod
    def _a_float(valor):
        try:
            return float(valor)
        except (TypeError, ValueError):
            return None


def build_client_from_env() -> SGAClient:
    return SGAClient(
        base_url=os.getenv("SGA_BASE_URL", "https://sgaestudiante.unemi.edu.ec"),
        access_token=os.getenv("SGA_ACCESS_TOKEN"),
        refresh_token=os.getenv("SGA_REFRESH_TOKEN"),
    )


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    client = build_client_from_env()

    if not client.access_token:
        print("Falta SGA_ACCESS_TOKEN en .env - inicia sesion en el navegador y cópialo.")
    else:
        try:
            materias = client.get_materias_parseadas()
            print(f"Conexion exitosa. {len(materias)} materias encontradas:\n")
            for m in materias:
                print(f"- {m['nombre']}")
                print(f"    Nota: {m['nota']}  |  Asistencia: {m['asistencia']}%")
        except Exception as e:
            print(f"Error al consultar: {e}")
            print("\nSi el error es 401, tu access_token expiro - repite el paso de "
                  "copiarlo desde DevTools.")

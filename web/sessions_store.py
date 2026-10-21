"""
Almacen de sesiones web en SQLite.

Reemplaza al diccionario en memoria (SESSIONS), que se perdia cada vez que
se reiniciaba el servidor y obligaba a todos a iniciar sesion de nuevo.

- Persistentes: las sesiones sobreviven a un reinicio del servidor.
- Con expiracion: por defecto 8 horas (variable SESSION_TTL_HORAS en .env).
- La cookie lleva un id aleatorio, pero en la base de datos solo se guarda
  su hash SHA-256: quien consiga el archivo .db no puede usar esos ids para
  suplantar una sesion.
- Los datos de la sesion (incluidos los tokens de Moodle y del ERP) se
  guardan CIFRADOS con Fernet. La llave sale de SESSION_ENC_KEY en .env o,
  si no existe, se genera sola la primera vez en el archivo .session_key
  (en la raiz del proyecto; esta en .gitignore). Si esa llave se pierde, las
  sesiones guardadas quedan ilegibles y simplemente se pide iniciar sesion.
"""
import base64
import hashlib
import json
import os
import secrets
import time

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "Falta la libreria 'cryptography'. Instala las dependencias con: "
        "pip install -r requirements.txt"
    ) from e

from db.setup import get_connection

TTL_SEGUNDOS = int(float(os.getenv("SESSION_TTL_HORAS", "8")) * 3600)
_RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SQL_TABLA = """
CREATE TABLE IF NOT EXISTS sesion_web (
    sid_hash TEXT PRIMARY KEY,
    estudiante_id INTEGER,
    datos_cifrados BLOB NOT NULL,
    creada REAL NOT NULL,
    expira REAL NOT NULL
)
"""

_fernet_cache = None


def _fernet() -> "Fernet":
    global _fernet_cache
    if _fernet_cache is None:
        llave = os.getenv("SESSION_ENC_KEY")
        if not llave:
            ruta = os.path.join(_RAIZ_PROYECTO, ".session_key")
            if os.path.exists(ruta):
                with open(ruta, "r", encoding="utf-8") as f:
                    llave = f.read().strip()
            else:
                llave = Fernet.generate_key().decode()
                with open(ruta, "w", encoding="utf-8") as f:
                    f.write(llave)
                try:
                    os.chmod(ruta, 0o600)
                except OSError:
                    pass
        try:
            _fernet_cache = Fernet(llave.encode())
        except ValueError:
            # No es una llave Fernet valida (44 caracteres en base64): se acepta cualquier
            # secreto largo y se deriva la llave de el (asi sirve un valor generado por el hosting).
            derivada = base64.urlsafe_b64encode(hashlib.sha256(llave.encode("utf-8")).digest())
            _fernet_cache = Fernet(derivada)
    return _fernet_cache


def _hash(sid: str) -> str:
    return hashlib.sha256(sid.encode("utf-8")).hexdigest()


def _conn():
    conn = get_connection()
    conn.execute(_SQL_TABLA)
    return conn


def crear(datos: dict) -> str:
    """Guarda una sesion nueva y devuelve el id que va en la cookie."""
    sid = secrets.token_urlsafe(32)
    ahora = time.time()
    cifrado = _fernet().encrypt(json.dumps(datos).encode("utf-8"))
    conn = _conn()
    try:
        # Aprovecha cada login para borrar las sesiones ya vencidas.
        conn.execute("DELETE FROM sesion_web WHERE expira < ?", (ahora,))
        conn.execute(
            "INSERT INTO sesion_web (sid_hash, estudiante_id, datos_cifrados, creada, expira) "
            "VALUES (?, ?, ?, ?, ?)",
            (_hash(sid), datos.get("estudiante_id"), cifrado, ahora, ahora + TTL_SEGUNDOS),
        )
        conn.commit()
    finally:
        conn.close()
    return sid


def obtener(sid: str):
    """Devuelve los datos de la sesion, o None si no existe, vencio o es ilegible."""
    if not sid:
        return None
    conn = _conn()
    try:
        fila = conn.execute(
            "SELECT datos_cifrados, expira FROM sesion_web WHERE sid_hash = ?", (_hash(sid),)
        ).fetchone()
        if not fila:
            return None
        if fila["expira"] < time.time():
            conn.execute("DELETE FROM sesion_web WHERE sid_hash = ?", (_hash(sid),))
            conn.commit()
            return None
        try:
            return json.loads(_fernet().decrypt(bytes(fila["datos_cifrados"])).decode("utf-8"))
        except (InvalidToken, ValueError):
            conn.execute("DELETE FROM sesion_web WHERE sid_hash = ?", (_hash(sid),))
            conn.commit()
            return None
    finally:
        conn.close()


def guardar(sid: str, datos: dict) -> None:
    """Actualiza los datos de una sesion existente (ej. tokens renovados), sin alargar su vida."""
    cifrado = _fernet().encrypt(json.dumps(datos).encode("utf-8"))
    conn = _conn()
    try:
        conn.execute(
            "UPDATE sesion_web SET datos_cifrados = ? WHERE sid_hash = ?", (cifrado, _hash(sid))
        )
        conn.commit()
    finally:
        conn.close()


def eliminar(sid: str) -> None:
    if not sid:
        return
    conn = _conn()
    try:
        conn.execute("DELETE FROM sesion_web WHERE sid_hash = ?", (_hash(sid),))
        conn.commit()
    finally:
        conn.close()

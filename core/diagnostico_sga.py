"""
Diagnostico del ERP (SGA): materias actuales + historial academico.

Sirve para ver POR QUE el asistente no muestra el historial o la nota oficial.
Imprime solo datos academicos (materia, periodo, nota, estado). Los textos que
traen nombre y cedula se enmascaran, asi que la salida se puede compartir.

Uso (con el entorno virtual activo, desde la carpeta del proyecto):
    python core\\diagnostico_sga.py

Te pide el token de acceso del ERP (el JWT largo, empieza por eyJ...).
No se imprime ni se guarda.
"""
import os
import re
import sys
import getpass

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from sga.client import SGAClient, extraer_nombre_materia, segundos_para_vencer


def _enmascarar(texto: str) -> str:
    """Quita el nombre del alumno (todo antes de la primera coma) y cualquier
    numero largo (cedula)."""
    texto = texto or ""
    if ", " in texto:
        texto = "[NOMBRE] , " + texto.split(", ", 1)[1]
    return re.sub(r"\d{8,}", "[#]", texto)


def _claves(d, n=25):
    return list(d.keys())[:n] if isinstance(d, dict) else type(d).__name__


def main():
    load_dotenv()
    print("Copia el token NUEVO del ERP desde el navegador (F12 > Network > cualquier")
    print("peticion a sgaestudiante... > Headers > Authorization, sin la palabra Bearer).")
    token = getpass.getpass("Pega el token (no se muestra; Enter = usar el del .env): ").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        token = os.getenv("SGA_ACCESS_TOKEN") or ""
    if not token:
        print("No hay token. Vuelve a correr el script y pega uno.")
        return
    refresh = os.getenv("SGA_REFRESH_TOKEN") or None
    restante = segundos_para_vencer(token)
    if restante is None:
        print("Aviso: no pude leer la fecha de vencimiento del token (puede estar incompleto o mal copiado).")
    elif restante <= 0:
        print(f"El token ya venció (hace {abs(restante)//60} min). Copia uno nuevo del navegador y vuelve a correr esto.")
        return
    else:
        print(f"Token válido por ~{restante//60} min más.")

    cli = SGAClient(os.getenv("SGA_BASE_URL", "https://sgaestudiante.unemi.edu.ec"), token, refresh)
    try:
        print("Cambio de perfil a Grado:", cli.cambiar_a_perfil_regular())
    except Exception as e:
        print("Aviso al cambiar de perfil:", e)

    print("\n=== 1) MATERIAS ACTUALES ===")
    raw = cli.get_materias()
    materias = raw.get("data", {}).get("eMateriasAsignadas", []) or []
    print("Cantidad:", len(materias))
    for i, m in enumerate(materias, 1):
        disp = m.get("display", "")
        print(f"\n--- Materia {i} ---")
        print("Texto (enmascarado):", _enmascarar(disp))
        print("Nombre extraido por el programa:", extraer_nombre_materia(disp))

    print("\n=== 2) ID DE ALUMNO PARA EL HISTORIAL ===")
    alumno_id = cli.get_alumno_id_automatico()
    print("ID detectado:", "SI" if alumno_id else "NO (por eso el historial queda vacio)")
    if materias:
        mat = materias[0].get("matricula", {})
        print("Claves de 'matricula':", _claves(mat))
        print("Claves de 'matricula.inscripcion':", _claves(mat.get("inscripcion", {}) if isinstance(mat, dict) else {}))
    if not alumno_id:
        return

    print("\n=== 3) HISTORIAL ACADEMICO ===")
    reg = cli.get_registro_academico(alumno_id)
    data = reg.get("data", {}) if isinstance(reg, dict) else {}
    print("Claves de la respuesta:", _claves(reg))
    print("Claves de 'data':", _claves(data))
    for k, v in (data.items() if isinstance(data, dict) else []):
        if isinstance(v, list):
            print(f"  data.{k}: lista de {len(v)} elementos")
    parsed = cli.get_registro_parseado(alumno_id)
    print("\nRegistros leidos por el programa:", len(parsed))
    for r in parsed:
        print(f"- {r['periodo']} | {r['materia']} | nota={r['nota']} | aprobada={r['aprobada']} | pendiente={r['pendiente']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Error:", e)

"""
Diagnostico del ERP (SGA): materias actuales + historial academico.

Sirve para ver POR QUE el asistente no muestra el historial o la nota oficial.
Por defecto imprime solo CANTIDADES y nombres de campos. Con --con-datos tambien
imprime materia, periodo, nota y estado (son tus datos academicos: revisalos antes
de compartirlos). Los textos con nombre y cedula siempre se enmascaran.

Uso (con el entorno virtual activo, desde la carpeta del proyecto):
    python herramientas/diagnostico/diagnostico_sga.py [--con-datos]

Te pide el token de acceso del ERP (el JWT largo, empieza por eyJ...). No se imprime
ni se guarda.
"""
import os
import sys
import getpass

import comun
from comun import imprimir, enmascarar
from sga.client import SGAClient, extraer_nombre_materia, segundos_para_vencer


def _claves(d, n=25):
    return list(d.keys())[:n] if isinstance(d, dict) else type(d).__name__


def main(con_datos: bool = False, cliente=None, token: str = None):
    from dotenv import load_dotenv
    load_dotenv()
    if cliente is None:
        print("Copia el token NUEVO del ERP desde el navegador (F12 > Network > cualquier")
        print("peticion a sgaestudiante... > Headers > Authorization, sin la palabra Bearer).")
        token = (token or getpass.getpass("Pega el token (no se muestra; Enter = usar el del .env): ")).strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            token = os.getenv("SGA_ACCESS_TOKEN") or ""
        if not token:
            print("No hay token. Vuelve a correr el script y pega uno.")
            return 1
        restante = segundos_para_vencer(token)
        if restante is None:
            print("Aviso: no pude leer la fecha de vencimiento del token (puede estar incompleto o mal copiado).")
        elif restante <= 0:
            print(f"El token ya venció (hace {abs(restante)//60} min). Copia uno nuevo y vuelve a correr esto.")
            return 1
        else:
            print(f"Token válido por ~{restante//60} min más.")
        cliente = SGAClient(os.getenv("SGA_BASE_URL", "https://sgaestudiante.unemi.edu.ec"), token,
                            os.getenv("SGA_REFRESH_TOKEN") or None)
    try:
        print("Cambio de perfil a Grado:", cliente.cambiar_a_perfil_regular())
    except Exception as e:
        imprimir("Aviso al cambiar de perfil:", e)

    print("\n=== 1) MATERIAS ACTUALES ===")
    raw = cliente.get_materias()
    materias = raw.get("data", {}).get("eMateriasAsignadas", []) or []
    print("Cantidad:", len(materias))
    for i, m in enumerate(materias, 1):
        disp = m.get("display", "")
        nombre = extraer_nombre_materia(disp)
        print(f"\n--- Materia {i} ---")
        print("Nombre extraido por el programa:", "SI" if nombre else "NO")
        if con_datos:
            imprimir("Texto (enmascarado):", enmascarar(disp))
            imprimir("Nombre extraido:", nombre)

    print("\n=== 2) ID DE ALUMNO PARA EL HISTORIAL ===")
    alumno_id = cliente.get_alumno_id_automatico()
    print("ID detectado:", "SI" if alumno_id else "NO (por eso el historial queda vacio)")
    if materias:
        mat = materias[0].get("matricula", {})
        print("Claves de 'matricula':", _claves(mat))
        print("Claves de 'matricula.inscripcion':", _claves(mat.get("inscripcion", {}) if isinstance(mat, dict) else {}))
    if not alumno_id:
        return 0

    print("\n=== 3) HISTORIAL ACADEMICO ===")
    reg = cliente.get_registro_academico(alumno_id)
    data = reg.get("data", {}) if isinstance(reg, dict) else {}
    print("Claves de la respuesta:", _claves(reg))
    print("Claves de 'data':", _claves(data))
    for k, v in (data.items() if isinstance(data, dict) else []):
        if isinstance(v, list):
            print(f"  data.{k}: lista de {len(v)} elementos")
    parsed = cliente.get_registro_parseado(alumno_id)
    print("\nRegistros leidos por el programa:", len(parsed))
    if con_datos:
        for r in parsed:
            imprimir(f"- {r['periodo']} | {r['materia']} | nota={r['nota']} | aprobada={r['aprobada']} | pendiente={r['pendiente']}")
    else:
        print("(usa --con-datos para ver materia, periodo y nota)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(con_datos="--con-datos" in sys.argv[1:]))
    except Exception as e:
        imprimir("Error:", e)
        sys.exit(1)

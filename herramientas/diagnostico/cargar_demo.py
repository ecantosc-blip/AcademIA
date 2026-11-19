"""
Carga una base de datos local con datos de ejemplo (ficticios) y deja lista
una sesion web ya iniciada, para poder mostrar o revisar la app SIN depender
de credenciales reales de Moodle/ERP ni de conexion a esos sistemas.

Usa el mismo estudiante de prueba (id 77) que ya usa la suite de pruebas
automaticas (ver tests/ayudas.py: sembrar_demo), para no mantener dos
copias de los mismos datos ficticios.

SOLO PARA REVISION LOCAL. No usar en un servidor publico: la base queda con
datos de ejemplo, no con datos de un estudiante real.

Uso:
    python herramientas/diagnostico/cargar_demo.py [ruta_db]

Si no se indica ruta, usa "./demo.db" en la raiz del proyecto.
"""
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RAIZ)


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RAIZ, "demo.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    for sufijo in ("-wal", "-shm"):
        extra = db_path + sufijo
        if os.path.exists(extra):
            os.remove(extra)

    os.environ["DB_PATH"] = db_path

    from db.setup import init_db
    init_db(db_path)

    sys.path.insert(0, os.path.join(RAIZ, "tests"))
    import ayudas
    ayudas.sembrar_demo(db_path)

    sys.path.insert(0, os.path.join(RAIZ, "web"))
    import sessions_store
    sid = sessions_store.crear({
        "estudiante_id": 77,
        "nombre": "ESTUDIANTE DE PRUEBA",
        "foto_url": "",
        "foto_erp_url": "",
        "moodle_token": "demo-no-valido",
        "sga_access_token": "",
        "sga_refresh_token": "",
        "sga_alumno_id": "",
    })

    print()
    print("Base de demostracion lista en:", db_path)
    print()
    print("Para levantar la app con esta base:")
    print(f'  DB_PATH="{db_path}" python web/app.py')
    print()
    print("Luego, en el navegador, abre http://127.0.0.1:5000, abre la consola")
    print("(F12 -> Console) y pega esta linea para entrar ya sin usar el login:")
    print()
    print(f'  document.cookie = "sid={sid}; path=/"; location.reload();')
    print()
    print("Nota: como el token de Moodle es falso, el boton 'Sincronizar' va a")
    print("fallar (es esperado). El resto de la app lee los datos ya cargados")
    print("en la base y funciona con normalidad.")


if __name__ == "__main__":
    main()

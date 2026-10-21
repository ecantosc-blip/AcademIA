"""
Muestra las tablas de la base de datos: sus columnas, cuál es la llave
primaria (PK), las llaves foráneas (FK) y cuántas filas tiene cada una.

Uso (desde la carpeta del proyecto):
    python ver_bd.py                  -> usa ./unemi_assistant.db
    python ver_bd.py otra_ruta.db     -> usa otra base de datos
"""
import sqlite3
import sys

ruta = sys.argv[1] if len(sys.argv) > 1 else "unemi_assistant.db"
conn = sqlite3.connect(ruta)

tablas = [r[0] for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
)]
print(f"Base de datos: {ruta}  ({len(tablas)} tablas)")

for tabla in tablas:
    filas = conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
    print(f"\n== {tabla}  ({filas} filas) ==")
    for _, nombre, tipo, no_nulo, _, es_pk in conn.execute(f"PRAGMA table_info({tabla})"):
        marcas = []
        if es_pk:
            marcas.append("PK")
        if no_nulo:
            marcas.append("NOT NULL")
        print(f"   {nombre:<24} {tipo or '-':<8} {' '.join(marcas)}")
    for fk in conn.execute(f"PRAGMA foreign_key_list({tabla})"):
        print(f"   FK: {fk[3]} -> {fk[2]}({fk[4]})")

conn.close()

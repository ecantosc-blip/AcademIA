# Pruebas de carga

Miden cuántos estudiantes puede atender la aplicación a la vez, sin gastar API ni molestar a UNEMI.

- `servidor_carga.py` arranca la aplicación **real** con Moodle, el ERP y la IA **simulados** (latencias configurables).
  Todo lo demás es real: rutas, sesiones cifradas, SQLite, límites, cupos, espacio de estudio.
- `carga.py` simula estudiantes: cada uno entra, y luego mira su panel, pregunta al asistente, hace prácticas,
  pide videos, consulta al tutor y actualiza datos, con pausas realistas. Sube de 10 a 200 usuarios por etapas.

```powershell
pip install -r pruebas\requirements-pruebas.txt

# Terminal 1
python pruebas\servidor_carga.py                       # opciones: --hilos 48 --sync-seg 1.5 --ia-seg 1.2 --con-limites
# Terminal 2
python pruebas\carga.py --etapas 10,25,50,100,200 --segundos 30
```

Resultados en `pruebas\resultados_<fecha>\`: `resumen.md` (tabla para la tesis) y `solicitudes.csv` (datos crudos).

## Qué variar para un buen capítulo de resultados

| Experimento | Cómo |
|---|---|
| Curva de usuarios | `--etapas 10,25,50,100,200` |
| Efecto de los hilos | repetir con `--hilos 8`, `--hilos 24`, `--hilos 48` |
| IA o UNEMI más lentos | `--ia-seg 4 --sync-seg 8` (valores más cercanos a la realidad) |
| Estrés puro | `carga.py --pensar 0.05,0.2` (casi sin pausas) |
| Efecto de los límites | `servidor_carga.py --con-limites` |
| Otro hardware | copiar el proyecto al servidor y repetir ahí |

## Cómo interpretar

- **p95**: el 95 % de las respuestas tarda eso o menos. Con la mezcla por defecto, el p95 global lo marca la latencia **simulada** de la IA;
  mira la tabla por acción para ver que panel y responder siguen en milisegundos.
- **Rechazadas (429/503)**: el servidor avisando "estoy ocupado" en vez de caerse. Es el comportamiento buscado.
- **Errores**: deben ser 0. Cualquier valor mayor es un problema real a investigar.
- Cliente y servidor comparten tu computadora: con muchos usuarios el cliente también consume CPU.

No uses `servidor_carga.py` con datos reales: acepta cualquier token.

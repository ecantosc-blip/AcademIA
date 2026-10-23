# Verificación de supuestos sobre Moodle y el ERP

Este documento separa lo que **está confirmado** de lo que **todavía es una hipótesis**.
Se actualiza con el resultado de `herramientas/diagnostico/verificar_moodle.py`, que
ejecuta el estudiante con su propio token (la salida no incluye nombres de cursos, de
ítems, ni tokens).

Estados: ✅ confirmado con cuenta real · 🟡 respaldado por documentación, sin probar con
la cuenta real · ❌ no confirmado.

## 1. Estado de entrega de una tarea

| Supuesto | Estado | Cómo se comprueba |
|---|---|---|
| La función `mod_assign_get_submission_status` existe (Moodle ≥ 3.1) | 🟡 Documentación: [Web service API functions](https://docs.moodle.org/dev/Web_service_API_functions) | — |
| Está habilitada en el servicio `moodle_mobile_app` de la UNEMI y el rol de estudiante puede llamarla | ✅ 23/10/2026: 6 de 6 consultas respondieron, 0 errores | El script de verificación la llama para hasta 6 tareas por curso y reporta éxito o el error |
| La respuesta trae `lastattempt.submission.status` con valores como `new`, `draft`, `submitted`, `reopened` | ✅ parcial: se observaron `submitted` (4) y `new` (1). `draft` y `reopened` **no se han visto** | El script cuenta los valores de `status` obtenidos |
| Una tarea sin objeto `submission` en la respuesta | 🟡 se observó 1 caso (`submission` ausente) que el sistema interpretó como «sin entregar»; **no se sabe aún si era una tarea grupal** (`teamsubmission`) ni si el estudiante realmente no entregó | Pendiente: confirmar contra la pantalla de Moodle |

**Cómo lo trata AcademIA mientras no esté confirmado:** si Moodle no responde o la
respuesta no tiene un estado reconocible, la tarea queda **«Sin verificar»**. Nunca se
interpreta como pendiente ni como entregada, y un estado ya verificado no se sobrescribe
si una consulta posterior falla.

## 2. Significado de `porcentaje` / `percentageformatted`

| Supuesto | Estado | Cómo se comprueba |
|---|---|---|
| `percentageformatted` es la nota como porcentaje del máximo del ítem (8 de 10 = 80 %), **no el peso** | ❌ en `gradereport_user_get_grade_items`: ese campo **no viene** (0 de 23 ítems, 9 con nota). Tampoco en `gradereport_user_get_grades_table`: la tabla solo trae `feedback`, `grade`, `itemname`, `leader`, `parentcategories` (51 filas, 8 con nota, 0 con rango, 0 con porcentaje, 0 con peso). **El porcentaje de Moodle no se puede interpretar porque no existe** | Para 2–3 ítems el script imprime nota, rango máximo, `percentageformatted` y `weightformatted`: si `porcentaje ≈ nota / máximo × 100` en todos, es avance |
| `weightformatted` (si existe) es el peso del ítem en la categoría o en el curso | ❌ **no viene** en `get_grade_items` (0 de 23). Tampoco hay columna `weight` en la tabla | Los pesos de una misma categoría deberían sumar ≈ 100 % |
| La nota máxima/mínima para aprobar es 100/70 | ❌ (suposición fija en `query_engine.nota_necesaria`) | Contraste con el modelo evaluativo del ERP |

**Conclusión (23/10/2026):** Moodle UNEMI no entrega pesos ni máximos por ítem al rol estudiante, ni por `get_grade_items` ni por `get_grades_table`. La columna `porcentaje` de `componente_nota` queda siempre vacía con datos reales. Cualquier peso o máximo para el simulador de notas tiene que venir de otra fuente (ERP, o declarado por el estudiante y marcado como tal).

**Regla de uso:** ningún cálculo (simulador de notas, indicador de seguimiento) usará
`porcentaje` como peso hasta que este apartado esté confirmado. El lector numérico
`core/numeros.py` ya existe y está probado, pero no participa todavía en cálculos.

## 3. Fechas

| Supuesto | Estado |
|---|---|
| Moodle entrega las fechas como timestamp Unix (segundos desde 1970 UTC) | 🟡 (así las devuelve `mod_assign_get_assignments`; el código las convertía con la zona del servidor) |
| Las fechas académicas se muestran en `America/Guayaquil` (UTC−5, sin horario de verano) | ✅ implementado y probado con datos simulados (ver `core/tiempo.py`) |

## Registro de verificaciones con cuenta real

| Fecha | Qué se comprobó | Resultado |
|---|---|---|
| 23/10/2026 | Estado de entrega (1 curso con tareas en línea, 6 tareas) | Función disponible para el rol estudiante. `submitted`=4, `new`=1, sin `submission`=1. Sin errores |
| 23/10/2026 | `percentageformatted` y `weightformatted` en `get_grade_items` | Ausentes en todos los ítems (23 ítems, 9 con nota). No sirven como fuente de pesos. Confirmado también en la tabla de calificaciones (sin `weight`, `range` ni `percentage`) |

# AcademIA Premium — revisión visual (rama de diseño)

Esta rama contiene únicamente ajustes de presentación. No se han cambiado las rutas Flask, las consultas a Moodle/SGA, los tokens ni el esquema SQLite.

## Comprobaciones antes de fusionar

- [ ] Iniciar sesión con una cuenta de prueba; no usar credenciales ni tokens reales en capturas.
- [ ] Inicio a 1440px: tarjetas KPI en una fila; agenda y recomendaciones en dos columnas.
- [ ] Inicio a 1024px: distribución en una columna para las secciones, sin desbordamiento horizontal.
- [ ] Inicio a 390px y 320px: KPI en dos columnas, accesos rápidos en tres, menú inferior desplazable.
- [ ] Revisar que alertas ocultas no reserven espacio y que aparezcan al activarse.
- [ ] Verificar enlaces de KPI, módulos, resumen semanal y chat.
- [ ] Revisar contraste y legibilidad en modo claro, oscuro y preferencia automática.
- [ ] Navegar con teclado; comprobar foco visible y menú móvil.
- [ ] Confirmar que Materias, Agenda, Novedades, Plan y Mis datos conservan su comportamiento.
- [ ] Verificar /salud y los registros de Flask tras desplegar en entorno de prueba.
- [ ] Comparar con main antes de abrir PR; no activar autodespliegue de producción para esta rama.

## Alcance técnico
- `web/static/academia-premium.css`: overrides responsive y navegación.
- `web/templates/index.html`: enlace a la hoja de estilos.
- Se conservan las variables institucionales existentes: `--brand` y `--orange`.

**Nota:** los cambios están versionados, pero no se han ejecutado pruebas de navegador ni validado Render en esta revisión.

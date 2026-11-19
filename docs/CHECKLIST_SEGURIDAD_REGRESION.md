# Checklist de seguridad y regresión — AcademIA

## Configuración obligatoria antes de producción

- Configurar `SESSION_ENC_KEY` como secreto persistente en Render. Conservar la misma clave entre despliegues y réplicas; no publicarla en Git.
- Configurar `COOKIE_SECURE=1` cuando el sitio se sirve exclusivamente por HTTPS.
- Configurar `TRUST_PROXY=1` **solo** si el servicio está detrás de un proxy confiable que sanea `X-Forwarded-*` (por ejemplo, la infraestructura de Render). Si no se conoce la cadena de proxies, no habilitarlo a ciegas.
- Mantener `ANTHROPIC_API_KEY` y `TELEGRAM_BOT_TOKEN` únicamente en secretos de entorno.
- Comprobar persistencia de la base SQLite y de la clave entre despliegues. Sin disco persistente o base externa, los datos pueden perderse.
- Verificar que los tokens de Moodle y SGA no aparezcan en logs, mensajes de error o URLs.

## Protección de operaciones de escritura

`web/app.py` rechaza peticiones con `Sec-Fetch-Site: cross-site`, `Origin: null` o un `Origin`/`Referer` de otro origen. Se aplica a login, logout y endpoints POST; no requiere cambios en los clientes existentes.

**Limitación:** la validación de origen es una mitigación CSRF de compatibilidad, no reemplaza un token CSRF explícito. Clientes antiguos que omiten todas esas cabeceras todavía pueden enviar POST. Antes de una adopción institucional, incorporar tokens CSRF por sesión en formularios y llamadas mutantes.

## Pruebas que deben ejecutarse

1. `python -m compileall -q web core db moodle sga`
2. `python -m pytest -q` (si pytest y dependencias están instalados).
3. En navegador con sesión de prueba: iniciar/cerrar sesión, sincronizar Moodle/SGA, consultar foto, chat, tareas, agenda, plan, mover actividad personal, práctica de estudio, borrar datos.
4. Confirmar que un POST legítimo desde el mismo origen funciona y que un POST con `Origin: https://ejemplo-malicioso.invalid` responde 403 sin modificar datos.
5. Verificar `Set-Cookie` con `HttpOnly`, `SameSite=Lax` y `Secure` bajo HTTPS; comprobar `Strict-Transport-Security` únicamente por HTTPS.
6. Verificar modo claro/oscuro, navegación compartida, foto y calendario en escritorio y móvil.
7. Verificar separación entre estudiantes con dos cuentas de prueba; ninguna debe acceder a datos de la otra.
8. Confirmar en Moodle los estados `submitted`, `new`, `draft`, tareas grupales y estados sin verificar.

## Estado

Estos pasos **no han sido ejecutados automáticamente** por esta revisión de GitHub. No afirmar que están aprobados hasta guardar evidencias reales. No usar credenciales ni tokens reales en capturas o incidencias.

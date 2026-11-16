/*
 * AcademIA - Token del ERP (extension del navegador)
 * -----------------------------------------------------------------------
 * Que hace: mientras el ERP de la UNEMI (sga.unemi.edu.ec / sgaestudiante.unemi.edu.ec)
 * esta abierto y la PERSONA ya inicio sesion normalmente en su propio navegador, esta
 * extension observa (sin modificar nada) las peticiones que el navegador envia a esos
 * dos dominios y guarda el valor del encabezado "Authorization" (el token de acceso)
 * para poder rellenarlo despues en el login de AcademIA con un clic.
 *
 * Que NO hace:
 *   - No inicia sesion por la persona ni completa el formulario de usuario/contrasena
 *     del ERP: el login normal (con su deteccion de bots) lo hace siempre un humano.
 *   - No lee ni envia nada a ningun otro sitio: host_permissions solo cubre esos dos
 *     dominios del ERP, y el token nunca sale de este navegador salvo cuando la propia
 *     persona pulsa "Rellenar en esta pestana" sobre la pagina de login de AcademIA.
 *   - No guarda la contrasena del ERP: esta extension nunca la ve.
 *
 * Nota sobre el aviso de Chrome: para leer tambien el token de RENOVACION (que solo viene en
 * el cuerpo de la respuesta del login, no en un encabezado), esta extension se conecta un
 * instante al Protocolo de Depuracion de Chrome sobre la pestana del ERP mientras la persona
 * inicia sesion. Chrome entonces muestra, el solo, un aviso ("...esta depurando este
 * navegador"): no se puede ocultar y es correcto que se vea, porque informa justo lo que esta
 * pasando. Dura solo unos segundos: la extension se desconecta apenas lee la respuesta (o a
 * los 20 s como maximo), y es exactamente la misma informacion que la persona veria a mano
 * abriendo F12 - Network - login.json.
 *
 * El token capturado se guarda solo en el almacenamiento local de la extension
 * (chrome.storage.local, dentro de este navegador) y se reemplaza cada vez que se ve
 * uno nuevo. Al desinstalar la extension, Chrome borra ese almacenamiento.
 */

const DOMINIOS_ERP = ["sga.unemi.edu.ec", "sgaestudiante.unemi.edu.ec"];

function esDominioErp(url) {
  try {
    const host = new URL(url).hostname;
    return DOMINIOS_ERP.some((d) => host === d || host.endsWith("." + d));
  } catch (e) {
    return false;
  }
}

chrome.webRequest.onSendHeaders.addListener(
  (detalle) => {
    if (!esDominioErp(detalle.url)) return;
    const cab = (detalle.requestHeaders || []).find(
      (h) => h.name.toLowerCase() === "authorization"
    );
    if (!cab || !cab.value) return;
    const token = cab.value.replace(/^Bearer\s+/i, "").trim();
    if (!token || token.split(".").length !== 3) return; // solo JWT de verdad (3 partes)
    chrome.storage.local.set({
      sga_access_token: token,
      sga_capturado_en: Date.now(),
    });
  },
  { urls: DOMINIOS_ERP.map((d) => `https://${d}/*`) },
  ["requestHeaders", "extraHeaders"]
);

// ------------------------------------------------------------------------------------------
// Token de renovacion ("refresh"): solo viene en el CUERPO de la respuesta de login.json, que
// chrome.webRequest no deja leer. Para traerlo tambien, se usa el Protocolo de Depuracion de
// Chrome (chrome.debugger) UNICAMENTE sobre la pestana donde la persona entra al ERP, y SOLO
// mientras dura el login: se conecta al llegar a la pagina, lee la respuesta de login.json en
// cuanto aparece y se desconecta al instante (o a los 20 s si no la ve). Mientras esta
// conectada, Chrome muestra su propio aviso ("...esta depurando este navegador"): es visible a
// proposito (Chrome no deja ocultarlo) y es la misma informacion que la persona veria a mano
// abriendo F12 - Network - login.json - Response, solo que automatico.
const DEBUGGER_VERSION = "1.3";
const depurando = new Set(); // tabIds con el depurador ya conectado (evita conectar dos veces)

function desconectar(tabId) {
  if (!depurando.has(tabId)) return;
  depurando.delete(tabId);
  chrome.debugger.detach({ tabId }, () => void chrome.runtime.lastError); // ignora "ya no esta"
}

function observarLogin(tabId) {
  if (depurando.has(tabId)) return;
  depurando.add(tabId);
  chrome.debugger.attach({ tabId }, DEBUGGER_VERSION, () => {
    if (chrome.runtime.lastError) { depurando.delete(tabId); return; } // ej. DevTools ya abierto
    // Se usa el dominio "Fetch" (no "Network"): pausa justo la respuesta de login.json el
    // tiempo minimo para leerla y la deja seguir su curso normal enseguida (Fetch.continueResponse).
    // Con "Network.getResponseBody" a secas el cuerpo de un fetch()/XHR puede desaparecer antes
    // de poder leerlo (coma Chrome lo descarta apenas la pagina lo consume); pausar la respuesta
    // evita esa carrera. El patron solo alcanza a login.json: el resto del trafico no se toca.
    chrome.debugger.sendCommand({ tabId }, "Fetch.enable", {
      patterns: [{ urlPattern: "*login.json*", requestStage: "Response" }],
    });
    setTimeout(() => desconectar(tabId), 20000); // por si nunca aparece login.json
  });
}

chrome.webNavigation.onCommitted.addListener(
  (detalle) => {
    if (detalle.frameId === 0 && esDominioErp(detalle.url)) observarLogin(detalle.tabId);
  },
  { url: DOMINIOS_ERP.map((d) => ({ hostEquals: d })) }
);

chrome.debugger.onEvent.addListener((fuente, metodo, params) => {
  if (metodo !== "Fetch.requestPaused" || !params.request.url.includes("login.json")) return;
  const tabId = fuente.tabId, requestId = params.requestId;
  const seguir = () => chrome.debugger.sendCommand({ tabId }, "Fetch.continueResponse", { requestId });
  chrome.debugger.sendCommand({ tabId }, "Fetch.getResponseBody", { requestId }, (resp) => {
    if (chrome.runtime.lastError || !resp) { seguir(); desconectar(tabId); return; }
    try {
      const texto = resp.base64Encoded ? atob(resp.body) : resp.body;
      const datos = JSON.parse(texto);
      const guardar = {};
      if (datos.access) { guardar.sga_access_token = datos.access; guardar.sga_capturado_en = Date.now(); }
      if (datos.refresh) { guardar.sga_refresh_token = datos.refresh; guardar.sga_refresh_capturado_en = Date.now(); }
      if (Object.keys(guardar).length) chrome.storage.local.set(guardar);
    } catch (e) {
      /* la respuesta no vino como se esperaba: no es grave, queda el token de acceso ya capturado */
    } finally {
      seguir();        // SIEMPRE se deja continuar la respuesta real: el login de la persona no debe quedar colgado
      desconectar(tabId);
    }
  });
});

chrome.tabs.onRemoved.addListener((tabId) => desconectar(tabId));

// Respaldo adicional: si el propio ERP renueva su sesion sola mientras la persona navega
// (sin pasar por el debugger), el refresh tambien viaja en el CUERPO de esa peticion.
chrome.webRequest.onBeforeRequest.addListener(
  (detalle) => {
    if (!esDominioErp(detalle.url) || !detalle.url.includes("/jwt/token/refresh")) return;
    const bruto = detalle.requestBody && detalle.requestBody.raw && detalle.requestBody.raw[0];
    if (!bruto || !bruto.bytes) return;
    try {
      const texto = new TextDecoder("utf-8").decode(bruto.bytes);
      const refresh = JSON.parse(texto).refresh;
      if (refresh) chrome.storage.local.set({ sga_refresh_token: refresh, sga_refresh_capturado_en: Date.now() });
    } catch (e) {
      /* cuerpo con otro formato: se ignora, no es grave (el refresh sigue siendo opcional) */
    }
  },
  { urls: DOMINIOS_ERP.map((d) => `https://${d}/*`) },
  ["requestBody"]
);

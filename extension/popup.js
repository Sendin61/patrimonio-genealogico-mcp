'use strict';

const ROB_SERVER = 'http://127.0.0.1:8877';

function mark(id, text, ok) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = ok ? 'ok' : 'bad';
}

async function refresh() {
  const errorEl = document.getElementById('error');
  let backendOk = false;
  let backendBridgeActive = false;
  let serviceWorkerOk = false;
  let bridge = null;

  try {
    const response = await fetch(`${ROB_SERVER}/api/status`, {cache: 'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    backendOk = true;
    backendBridgeActive = Boolean(status.familysearch_bridge?.connected);
    mark('backend', 'ROB local: conectado', true);
    mark('worker', `Puente backend: ${backendBridgeActive ? 'activo' : 'esperando extensión'}`, backendBridgeActive);
  } catch (error) {
    mark('backend', 'ROB local: no disponible', false);
    mark('worker', 'Puente backend: sin conexión', false);
    errorEl.textContent = `ROB local: ${String(error)}`;
  }

  try {
    bridge = await chrome.runtime.sendMessage({type: 'BRIDGE_STATUS'});
    serviceWorkerOk = Boolean(bridge?.ok && bridge?.loopRunning);
    const tabCount = bridge?.familySearchTabs || 0;
    mark('fs', `Pestañas FamilySearch: ${tabCount}`, tabCount > 0);
  } catch (error) {
    mark('fs', 'No se pudo consultar el service worker', false);
    if (backendOk) errorEl.textContent = `Extensión: ${String(error)}`;
  }

  // A transient/stale error must never overwrite a currently healthy state.
  const healthy = backendOk && backendBridgeActive && serviceWorkerOk && (bridge?.familySearchTabs || 0) > 0;
  if (healthy) {
    errorEl.textContent = '';
    return;
  }

  if (bridge?.lastBridgeError) {
    const ageMs = Date.now() - Number(bridge.lastBridgeErrorAt || 0);
    // Only surface a recent error while the bridge is actually unhealthy.
    if (ageMs >= 0 && ageMs < 15000) {
      errorEl.textContent = `Último error del puente: ${bridge.lastBridgeError}`;
    } else if (backendOk) {
      errorEl.textContent = '';
    }
  } else if (backendOk && serviceWorkerOk) {
    errorEl.textContent = '';
  }
}

document.getElementById('start').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({type: 'BRIDGE_START'});
  await new Promise(resolve => setTimeout(resolve, 350));
  refresh();
});

refresh();
setInterval(refresh, 2500);

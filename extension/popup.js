'use strict';

const ROB_SERVER = 'http://127.0.0.1:8877';

function mark(id, text, ok) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = ok ? 'ok' : 'bad';
}

async function refresh() {
  document.getElementById('error').textContent = '';
  try {
    const response = await fetch(`${ROB_SERVER}/api/status`, {cache: 'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const status = await response.json();
    mark('backend', 'ROB local: conectado', true);
    mark('worker', `Puente backend: ${status.familysearch_bridge?.connected ? 'activo' : 'esperando extensión'}`, Boolean(status.familysearch_bridge?.connected));
  } catch (error) {
    mark('backend', 'ROB local: no disponible', false);
    document.getElementById('error').textContent = String(error);
  }

  try {
    const bridge = await chrome.runtime.sendMessage({type: 'BRIDGE_STATUS'});
    mark('fs', `Pestañas FamilySearch: ${bridge.familySearchTabs || 0}`, (bridge.familySearchTabs || 0) > 0);
    if (bridge.lastBridgeError) {
      document.getElementById('error').textContent = `Último error del puente: ${bridge.lastBridgeError}`;
    }
  } catch (error) {
    mark('fs', 'No se pudo consultar el service worker', false);
  }
}

document.getElementById('start').addEventListener('click', async () => {
  await chrome.runtime.sendMessage({type: 'BRIDGE_START'});
  await new Promise(resolve => setTimeout(resolve, 350));
  refresh();
});

refresh();
setInterval(refresh, 2500);

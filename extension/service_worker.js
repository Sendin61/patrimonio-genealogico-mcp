'use strict';

const ROB_SERVER = 'http://127.0.0.1:8877';
let loopRunning = false;
let stopRequested = false;

function isFamilySearchUrl(value) {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    return url.protocol === 'https:' && (host === 'familysearch.org' || host.endsWith('.familysearch.org'));
  } catch {
    return false;
  }
}

function isImageArk(value) {
  return /^3:[12]:[A-Z0-9-]+$/i.test(String(value || '').trim());
}

async function familySearchTabs() {
  const tabs = await chrome.tabs.query({});
  return tabs.filter(tab => isFamilySearchUrl(tab.url || ''));
}

async function preferredFamilySearchTab() {
  const active = await chrome.tabs.query({active: true, currentWindow: true});
  const activeFS = active.find(tab => isFamilySearchUrl(tab.url || ''));
  if (activeFS) return activeFS;
  const tabs = await familySearchTabs();
  return tabs[0] || null;
}

async function pageFetch(tabId, url, init = {}) {
  if (!isFamilySearchUrl(url)) {
    throw new Error('ROB bloqueó una URL que no pertenece a FamilySearch.');
  }

  const results = await chrome.scripting.executeScript({
    target: {tabId},
    world: 'MAIN',
    func: async (targetUrl, requestInit) => {
      try {
        const options = {
          method: requestInit?.method || 'GET',
          credentials: 'include',
          headers: {
            'Accept': 'application/json, text/plain, */*',
            ...(requestInit?.headers || {})
          }
        };
        if (typeof requestInit?.body === 'string') options.body = requestInit.body;
        const response = await fetch(targetUrl, options);
        const text = await response.text();
        return {
          ok: response.ok,
          status: response.status,
          statusText: response.statusText,
          url: response.url,
          contentType: response.headers.get('content-type') || '',
          text
        };
      } catch (error) {
        return {ok: false, status: 0, url: targetUrl, error: String(error), text: ''};
      }
    },
    args: [url, init]
  });

  return results?.[0]?.result || {ok: false, status: 0, url, error: 'Sin resultado de la pestaña.', text: ''};
}

function parseMaybeJson(result) {
  let json = null;
  try { json = JSON.parse(result.text || ''); } catch {}
  return {...result, json};
}

function clampInt(value, min, max, fallback) {
  const n = Number.parseInt(String(value ?? ''), 10);
  return Number.isFinite(n) ? Math.max(min, Math.min(max, n)) : fallback;
}

function queryUrl(base, params = {}) {
  const url = new URL(base);
  for (const [key, raw] of Object.entries(params || {})) {
    if (raw === undefined || raw === null || raw === '') continue;
    if (Array.isArray(raw)) {
      for (const item of raw) url.searchParams.append(key, String(item));
    } else {
      url.searchParams.set(key, String(raw));
    }
  }
  return url.toString();
}

async function runCommand(command) {
  const tab = await preferredFamilySearchTab();
  if (!tab?.id) {
    return {ok: false, error: 'No hay ninguna pestaña normal de FamilySearch abierta.'};
  }

  const payload = command.payload || {};

  if (command.type === 'ping') {
    return {
      ok: true,
      payload: {
        tab_id: tab.id,
        tab_url: tab.url || '',
        tab_title: tab.title || '',
        bridge_mode: 'chrome.scripting-main-world'
      }
    };
  }

  let result;

  if (command.type === 'fulltext_search') {
    const query = String(payload.query || '').trim();
    if (!query) return {ok: false, error: 'Consulta full-text vacía.'};
    const count = clampInt(payload.count, 1, 100, 100);
    const offset = clampInt(payload.offset, 0, 100000000, 0);
    const params = {
      count,
      'm.defaultFacets': 'on',
      'm.queryRequireDefault': 'on',
      offset,
      'q.text': query,
      ...(payload.params && typeof payload.params === 'object' ? payload.params : {})
    };
    result = await pageFetch(tab.id, queryUrl('https://www.familysearch.org/service/search/fulltext/search', params));
  } else if (command.type === 'record_search') {
    const params = payload.params && typeof payload.params === 'object' ? payload.params : {};
    result = await pageFetch(tab.id, queryUrl('https://www.familysearch.org/service/search/hr/v2/personas', params));
  } else if (command.type === 'tree_person') {
    const pid = String(payload.pid || '').trim().toUpperCase();
    if (!/^[A-Z0-9-]{4,20}$/.test(pid)) return {ok: false, error: 'PID de FamilySearch no válido.'};
    const url = `https://www.familysearch.org/service/tree/tree-data/v8/person/${encodeURIComponent(pid)}/details`;
    result = await pageFetch(tab.id, url);
  } else if (command.type === 'structured_ocr' || command.type === 'ocr_page') {
    const imageId = String(payload.image_id || '').trim();
    if (!isImageArk(imageId)) return {ok: false, error: 'Identificador de imagen OCR no válido.'};
    const requestedHost = String(payload.host || 'https://sg30p0.familysearch.org').replace(/\/$/, '');
    if (!isFamilySearchUrl(requestedHost + '/')) return {ok: false, error: 'Host OCR no válido.'};
    const url = `${requestedHost}/service/records/volunteer/orchestration/sls/image/records/${imageId}`;
    result = await pageFetch(tab.id, url);
  } else if (command.type === 'ocr_pages') {
    const items = Array.isArray(payload.items) ? payload.items.slice(0, 25) : [];
    if (!items.length) return {ok: false, error: 'No se indicaron páginas OCR.'};
    const pages = [];
    await Promise.all(items.map(async item => {
      const imageId = String(item?.image_id || '').trim();
      const host = String(item?.host || 'https://sg30p0.familysearch.org').replace(/\/$/, '');
      if (!isImageArk(imageId) || !isFamilySearchUrl(host + '/')) {
        pages.push({image_id: imageId, ok: false, error: 'Identificador/host no válido.'});
        return;
      }
      const url = `${host}/service/records/volunteer/orchestration/sls/image/records/${imageId}`;
      const page = parseMaybeJson(await pageFetch(tab.id, url));
      pages.push({image_id: imageId, ...page});
    }));
    const order = new Map(items.map((item, index) => [String(item?.image_id || '').trim(), index]));
    pages.sort((a, b) => (order.get(a.image_id) ?? 9999) - (order.get(b.image_id) ?? 9999));
    return {ok: true, payload: {pages}};
  } else if (command.type === 'familysearch_fetch' || command.type === 'image_metadata') {
    const url = String(payload.url || '').trim();
    if (!isFamilySearchUrl(url)) return {ok: false, error: 'URL FamilySearch no válida.'};
    const init = payload.init && typeof payload.init === 'object' ? payload.init : {};
    result = await pageFetch(tab.id, url, init);
  } else {
    return {ok: false, error: `Orden no soportada: ${command.type}`};
  }

  const parsed = parseMaybeJson(result);
  return {
    ok: Boolean(parsed.ok),
    payload: {
      status: parsed.status,
      status_text: parsed.statusText || '',
      url: parsed.url || '',
      content_type: parsed.contentType || '',
      json: parsed.json,
      text: parsed.json === null ? (parsed.text || '') : null
    },
    error: parsed.ok ? null : (parsed.error || `FamilySearch respondió HTTP ${parsed.status}`)
  };
}

async function sendResult(commandId, result) {
  await fetch(`${ROB_SERVER}/bridge/result`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      command_id: commandId,
      ok: Boolean(result.ok),
      payload: result.payload || {},
      error: result.error || null
    })
  });
}

async function bridgeLoop() {
  if (loopRunning) return;
  loopRunning = true;
  stopRequested = false;

  try {
    while (!stopRequested) {
      try {
        const response = await fetch(`${ROB_SERVER}/bridge/next?wait=20`, {cache: 'no-store'});
        if (!response.ok) throw new Error(`ROB backend HTTP ${response.status}`);
        const data = await response.json();
        const command = data?.command;
        if (!command?.id) continue;

        let result;
        try {
          result = await runCommand(command);
        } catch (error) {
          result = {ok: false, error: String(error)};
        }
        await sendResult(command.id, result);
      } catch (error) {
        await chrome.storage.local.set({lastBridgeError: String(error), lastBridgeErrorAt: Date.now()});
        await new Promise(resolve => setTimeout(resolve, 1500));
      }
    }
  } finally {
    loopRunning = false;
  }
}

chrome.runtime.onInstalled.addListener(() => bridgeLoop());
chrome.runtime.onStartup.addListener(() => bridgeLoop());
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (message?.type === 'BRIDGE_START') {
      stopRequested = false;
      bridgeLoop();
      return sendResponse({ok: true});
    }
    if (message?.type === 'BRIDGE_STATUS') {
      const tabs = await familySearchTabs();
      const stored = await chrome.storage.local.get(['lastBridgeError', 'lastBridgeErrorAt']);
      return sendResponse({
        ok: true,
        loopRunning,
        familySearchTabs: tabs.length,
        lastBridgeError: stored.lastBridgeError || null,
        lastBridgeErrorAt: stored.lastBridgeErrorAt || null
      });
    }
    return sendResponse({ok: false, error: 'Mensaje desconocido'});
  })();
  return true;
});

bridgeLoop();

from __future__ import annotations

import asyncio
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from .ai import LlamaCppRuntime, interpret_research_request
from .bridge import BridgeCommand, BridgeCommandType, BridgeResult, InMemoryBridgeQueue
from .config import lab_port, resolve_lab_paths


bridge = InMemoryBridgeQueue()
paths = resolve_lab_paths(create=True)
ai_runtime = LlamaCppRuntime()
_ai_start_task: asyncio.Task[bool] | None = None


APP_HTML = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ROB Genealogy Lab</title>
<style>
:root{font-family:Inter,Segoe UI,system-ui,sans-serif;color-scheme:dark}
body{margin:0;background:#111315;color:#f3f3f3}
main{max-width:1080px;margin:0 auto;padding:28px}
header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;gap:12px}
.badges{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.badge{padding:7px 10px;border-radius:999px;background:#252a2e;font-size:13px}
.card{background:#191d20;border:1px solid #2d3338;border-radius:14px;padding:18px;margin-bottom:14px}
textarea{width:100%;box-sizing:border-box;min-height:150px;resize:vertical;border:1px solid #343a40;border-radius:12px;background:#0f1113;color:#fff;padding:14px;font:16px/1.45 inherit}
button{margin-top:12px;border:0;border-radius:10px;padding:11px 16px;font-weight:650;cursor:pointer}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
pre{white-space:pre-wrap;word-break:break-word;background:#0f1113;padding:14px;border-radius:10px;min-height:100px;max-height:520px;overflow:auto}
small{color:#a8b0b7}
@media(max-width:760px){.grid{grid-template-columns:1fr}header{align-items:flex-start;flex-direction:column}.badges{justify-content:flex-start}}
</style>
</head>
<body><main>
<header>
<div><h1 style="margin:0">ROB Genealogy Lab</h1><small>Investigador genealógico local-first</small></div>
<div class="badges"><div id="ai" class="badge">IA local: comprobando…</div><div id="bridge" class="badge">FamilySearch: comprobando…</div></div>
</header>
<section class="card">
<label for="prompt"><strong>¿Qué quieres investigar?</strong></label>
<textarea id="prompt" placeholder="Ej.: buscame los padres d andres quintas barela x arzua, creo q vivio sobre 1800. ojo q el OCR puede estar destrozado y mira varias paginas antes d concluir."></textarea>
<button id="go">Interpretar y preparar investigación</button>
</section>
<div class="grid">
<section class="card"><strong>Interpretación</strong><pre id="request">Todavía no hay una investigación activa.</pre></section>
<section class="card"><strong>Actividad</strong><pre id="activity">Esperando.</pre></section>
</div>
<script>
async function status(){
 try{
  const r=await fetch('/api/status'); const j=await r.json();
  document.getElementById('bridge').textContent='FamilySearch: '+(j.familysearch_bridge.connected?'conectado':'sin puente');
  const ai=j.local_ai||{};
  document.getElementById('ai').textContent='IA local: '+(ai.running?(ai.provider?.model||'lista'):(ai.model_file?'arrancando/no disponible':'sin modelo'));
 }catch(e){document.getElementById('activity').textContent='No se pudo consultar el backend local.'}
}
setInterval(status,2500); status();
document.getElementById('go').onclick=async()=>{
 const text=document.getElementById('prompt').value.trim(); if(!text)return;
 document.getElementById('activity').textContent='Interpretando tu petición con la IA local…';
 const r=await fetch('/api/research/prepare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
 const j=await r.json();
 document.getElementById('request').textContent=JSON.stringify(j.interpretation||j,null,2);
 document.getElementById('activity').textContent=j.next_step||j.error||'Preparada.';
 status();
};
</script>
</main></body></html>"""


async def _background_ai_start() -> None:
    global _ai_start_task
    if _ai_start_task and not _ai_start_task.done():
        return
    _ai_start_task = asyncio.create_task(ai_runtime.ensure_running())


async def homepage(request: Request) -> HTMLResponse:
    return HTMLResponse(APP_HTML)


async def status(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "lab_root": str(paths.root),
            "familysearch_bridge": {
                "connected": bridge.connected,
                "last_seen_at": bridge.last_extension_seen_at,
            },
            "local_ai": await ai_runtime.status(),
        }
    )


async def prepare_research(request: Request) -> JSONResponse:
    body = await request.json()
    text = str(body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "Petición vacía."}, status_code=400)

    ai_status = await ai_runtime.provider.status()
    if not ai_status.available:
        await _background_ai_start()
        return JSONResponse(
            {
                "raw_request": text,
                "interpretation_status": "waiting_local_ai",
                "local_ai": await ai_runtime.status(),
                "evidence_policy": "raw_ocr_never_overwritten",
                "document_context_policy": "adaptive_multipage_required",
                "next_step": (
                    "La petición se ha conservado. Falta preparar/arrancar el modelo GGUF local; "
                    "no se usará ninguna API de pago como sustituto oculto."
                ),
            },
            status_code=503,
        )

    try:
        interpretation = await interpret_research_request(ai_runtime.provider, text)
    except Exception as exc:
        return JSONResponse(
            {
                "raw_request": text,
                "interpretation_status": "local_ai_error",
                "error": str(exc),
                "next_step": "Revisar llama-server.log; la petición original se conserva intacta.",
            },
            status_code=502,
        )

    return JSONResponse(
        {
            "raw_request": text,
            "interpretation_status": "interpreted_local",
            "interpretation": interpretation,
            "evidence_policy": "raw_ocr_never_overwritten",
            "document_context_policy": "adaptive_multipage_required",
            "next_step": "Generar plan adaptativo y ejecutarlo mediante el puente FamilySearch.",
        }
    )


async def bridge_next(request: Request) -> JSONResponse:
    command = bridge.next_command()
    return JSONResponse({"command": command.to_dict() if command else None})


async def bridge_result(request: Request) -> JSONResponse:
    body = await request.json()
    command_id = str(body.get("command_id") or "").strip()
    if not command_id:
        return JSONResponse({"error": "command_id requerido"}, status_code=400)
    result = BridgeResult(
        command_id=command_id,
        ok=bool(body.get("ok")),
        payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
        error=str(body.get("error") or "") or None,
    )
    bridge.complete(result)
    return JSONResponse({"ok": True})


async def bridge_test_command(request: Request) -> JSONResponse:
    command = bridge.enqueue(BridgeCommand(type=BridgeCommandType.PING))
    return JSONResponse({"command": command.to_dict()})


async def startup() -> None:
    await _background_ai_start()


app = Starlette(
    debug=False,
    on_startup=[startup],
    routes=[
        Route("/", homepage, methods=["GET"]),
        Route("/api/status", status, methods=["GET"]),
        Route("/api/research/prepare", prepare_research, methods=["POST"]),
        Route("/bridge/next", bridge_next, methods=["GET"]),
        Route("/bridge/result", bridge_result, methods=["POST"]),
        Route("/api/bridge/test", bridge_test_command, methods=["POST"]),
    ],
)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=lab_port(), log_level="info")


if __name__ == "__main__":
    main()

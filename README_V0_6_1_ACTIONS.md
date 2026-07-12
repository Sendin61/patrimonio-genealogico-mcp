# Rob v0.6.1 — puente para GPT Actions

Añade al mismo servidor, sin eliminar el MCP:

- `GET /health`
- `GET /openapi.json`
- `GET /privacy`
- `POST /api/galiciana/investigar`
- `POST /api/galiciana/leer-pagina`

El esquema OpenAPI incluye un `servers` HTTPS válido para que pueda importarse en un GPT personalizado de ChatGPT Plus.

La búsqueda REST devuelve una respuesta compacta. La lectura completa se hace página por página mediante ALTO/METS, que es el flujo ya verificado en vivo.

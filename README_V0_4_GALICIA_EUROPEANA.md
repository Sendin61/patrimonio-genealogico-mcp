# Rob v0.4.0 — Galicia funcional mediante Europeana

## Decisión

El SPARQL público de Galiciana responde a consultas mínimas, pero las
búsquedas de texto desde Render son demasiado lentas e inestables para
servir como motor interactivo.

La búsqueda principal de Galicia utiliza ahora la API estable de
Europeana y limita los resultados a:

- Data provider: `Galiciana: Digital Library of Galicia`
- Collection: `2022706_Ag_ES_Hispana_gal`

El SPARQL se conserva como fuente auxiliar y experimental.

## Nueva herramienta MCP

`buscar_galicia_europeana`

Busca nombres y variantes en los metadatos de Galiciana cosechados por
Europeana. Todavía no realiza búsqueda dentro del OCR de periódicos y
libros.

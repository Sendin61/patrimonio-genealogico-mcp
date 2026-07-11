# Rob v0.3 — Galicia, primera instalación

## Instalado

### Galiciana. Biblioteca Dixital de Galicia

- Conector SPARQL para buscar en metadatos abiertos.
- Búsqueda por nombre, variantes, lugares y límites cronológicos.
- Normalización al modelo común de resultados.
- Herramienta MCP: `buscar_galiciana_metadatos`.

### Galiciana. Biblioteca y Arquivo Dixital

- Cliente OAI-PMH reutilizable.
- Comprobación `Identify` de ambos repositorios.
- Herramienta MCP: `comprobar_galicia`.

## Lo que todavía no debe fingirse

El conector SPARQL busca en metadatos: título, autor, descripción y lugar.
No busca todavía nombres enterrados en el OCR de una página de periódico.

El Arquivo Dixital tiene OAI-PMH, pero OAI-PMH sirve para recolectar
metadatos, no para ejecutar búsquedas arbitrarias por persona. Su búsqueda
completa requerirá:

1. Integrar de forma estable el formulario público, o
2. Recolectar los metadatos OAI-PMH en un índice propio.

La segunda opción es más limpia y reutilizable para otras comunidades.

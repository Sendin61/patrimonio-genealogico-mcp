# Rob v0.3.3 — plan de consulta real para Galiciana

## Qué estaba mal

La versión anterior acortaba la URL, pero seguía ejecutando una
operación costosa: concatenaba título, autor, descripción y lugar para
cada objeto del grafo y después aplicaba `CONTAINS`. Repetir ese barrido
para varias variantes podía agotar el tiempo de respuesta. Un timeout
de `httpx` podía además mostrarse en el Inspector como un error vacío.

## Qué cambia

- Usa el grafo de objetos digitales como `default-graph-uri`.
- Sigue los patrones publicados por Galiciana:
  `dc:creator/skos:prefLabel`, `dc:title` y `dc:description`.
- Consulta cada campo directamente antes de aplicar el filtro.
- Ya no mezcla nombre, cónyuge y lugares en una cadena artificial.
- Captura timeouts y errores por consulta.
- Devuelve resultados parciales y un diagnóstico explícito en lugar
  de abortar toda la herramienta con un mensaje vacío.
- Las fechas se filtran de forma segura después de recibir metadatos.

Esta versión todavía no consulta el OCR interno de las páginas.

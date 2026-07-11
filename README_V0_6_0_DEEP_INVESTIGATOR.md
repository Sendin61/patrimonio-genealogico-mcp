# Rob v0.6.0 — investigación profunda automática en Galiciana

Esta versión integra la lectura METS/ALTO ya verificada dentro de `investigar_persona_galiciana`.

## Cambios

- La herramienta puede leer automáticamente el OCR completo de hasta 40 páginas por defecto.
- Extrae ventanas amplias alrededor del nombre aunque el nombre esté dividido entre líneas.
- Recalcula puntuación, categorías y cronología usando el texto completo, no solo el fragmento del buscador.
- Devuelve `evidencias_documentales` con fecha, obra, página, contexto, URLs de página, OCR e imagen.
- Informa cuántas páginas completas se solicitaron, se leyeron y fallaron.
- Mantiene los resultados de búsqueda aunque una lectura individual falle.
- Limita la concurrencia para no sobrecargar Galiciana.

## Parámetros nuevos

- `leer_paginas_completas` (por defecto: `true`)
- `max_paginas_completas` (por defecto: `40`, máximo: `100`)
- `concurrencia_lectura` (por defecto: `2`, máximo: `4`)

## Pruebas

15 pruebas superadas.

# Rob v0.5.0 — Galiciana OCR real

Esta versión deja de tratar Europeana o SPARQL como buscadores principales de
personas en Galicia. El conector principal reproduce el formulario público de
búsqueda a texto completo de Galiciana:

- `POST /es/consulta/resultados_ocr.do`
- `general_ocr=on`
- `busq_general=<consulta>`

También resuelve la pantalla JavaScript anti-bot sin copiar ni conservar las
cookies del usuario: abre su propia sesión, desempaqueta el desafío y envía su
propio `fwb_dat`.

## Herramienta principal

`investigar_persona_galiciana`

- genera consultas exactas, variantes sin tildes y una búsqueda tolerante a OCR;
- recorre las páginas de resultados;
- extrae obra, publicación, fecha, página, fragmentos OCR y enlaces;
- elimina duplicados;
- puntúa posibles homónimos con fechas, lugares, cónyuge y profesión;
- crea una cronología y agrupa indicios militares, judiciales, municipales,
  patrimoniales, familiares y de vecindad.

Los conocimientos previos del usuario solo sirven para desambiguar. Los hechos
devueltos se apoyan exclusivamente en evidencias recuperadas de Galiciana.

## Lectura profunda

`leer_pagina_galiciana` abre uno de los enlaces devueltos e intenta recuperar
bloques OCR, texto visible, imágenes y PDF. Algunos visores pueden exponer solo
la imagen; ese caso queda identificado para la siguiente fase de lectura visual.

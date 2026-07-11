# Rob v0.5.4 — lector METS tolerante

Corrige la fase de lectura completa de páginas de Galiciana:

- distingue una página HTML de exportación de un METS XML real;
- sigue automáticamente formularios intermedios GET/POST;
- repara ampersands sin escapar, BOM y caracteres de control en METS/ALTO;
- descarta falsos enlaces como variables JavaScript `linkMETS`;
- mantiene la búsqueda OCR, deduplicación y filtro cronológico ya verificados.

Prueba remota: ejecutar `leer_pagina_galiciana` con la URL de una página devuelta por `investigar_persona_galiciana`.

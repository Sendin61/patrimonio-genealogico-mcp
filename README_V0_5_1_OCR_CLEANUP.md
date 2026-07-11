# Rob v0.5.1 — limpieza de la investigación OCR

La prueba real desde Render confirmó que el conector accede al OCR de
Galiciana, pero reveló cuatro defectos de la v0.5.0:

1. el desafío anti-bot podía cambiar de forma y ocultar `fwb_dat`;
2. una desconexión del servidor abortaba una consulta sin reintento;
3. la misma página se contaba varias veces si la devolvían varias variantes;
4. el intervalo de fechas solo afectaba a la puntuación y no filtraba.

La v0.5.1:

- extrae el payload anti-bot también por inspección del contenido base64;
- reintenta desconexiones transitorias;
- abre una sesión independiente por consulta;
- considera cada página física una sola mención y fusiona sus fragmentos;
- excluye de verdad las fechas fuera del intervalo indicado;
- evita consultas amplias cuando las frases exactas ya han producido una
  cantidad suficiente de resultados;
- marca la fuente OCR como verificada tras la prueba real desde Render.

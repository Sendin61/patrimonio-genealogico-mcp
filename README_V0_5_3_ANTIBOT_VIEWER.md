# Rob v0.5.3 — estabilización del visor Galiciana

Corrige el fallo `No se pudo decodificar la sesión anti-bot` observado al abrir páginas y descargar METS.

Cambios principales:

- conserva el valor de cada variable JavaScript antes de que el desafío reutilice nombres obfuscados;
- acepta desafíos correspondientes al visor, METS y otros endpoints `.do`, no solo al buscador OCR;
- detecta el endpoint real a partir de la petición HTTP codificada en `fwb_dat`;
- admite Base64 normal y URL-safe;
- inicia una sesión de navegación antes de abrir el visor;
- reintenta tres veces la apertura de la página;
- devuelve un resultado estructurado `unavailable` en vez de romper la herramienta si la protección vuelve a cambiar;
- mantiene intacta la búsqueda OCR ya verificada en v0.5.1.

Prueba local: 11 tests superados.

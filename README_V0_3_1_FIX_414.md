# Rob v0.3.1 — corrección HTTP 414

La consulta SPARQL de Galiciana se envía ahora mediante HTTP POST.

Antes se utilizaba GET, lo que colocaba la consulta completa en la URL.
Las búsquedas genealógicas con variantes, lugares y fechas podían superar
el límite del servidor y producir `414 Request-URI Too Long`.

No cambia la lógica de búsqueda ni los datos enviados: solo el transporte.

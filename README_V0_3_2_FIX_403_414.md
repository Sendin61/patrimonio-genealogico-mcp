# Rob v0.3.2 — compatibilidad real con el SPARQL de Galiciana

## Problema observado

- Una consulta larga enviada mediante GET devolvía HTTP 414.
- El intento de enviarla mediante POST devolvía HTTP 403.

## Solución

Rob vuelve a usar GET, que es la vía que acepta el formulario público de
Galiciana, pero divide la búsqueda en varias consultas compactas: una por
variante prioritaria del nombre.

Los resultados se fusionan, se eliminan duplicados y se ordenan por puntuación.

Esta solución evita fingir que Galiciana ofrece un POST abierto cuando el
servidor lo está rechazando.

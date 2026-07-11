# Rob v0.5.5 — exportación METS real de Galiciana

Esta revisión adapta el lector al HTML real de la pantalla **Exportar a METS** de Galiciana.

## Corrección principal

El enlace final no usa `mets.do`, sino:

```text
/es/media/group/export-mets.do?path=...&destination=...
```

La versión 0.5.4 veía el botón **Exportar**, pero descartaba su URL porque el filtro solo aceptaba `mets.do`. La 0.5.5:

- reconoce `export-mets.do` como recurso METS válido;
- conserva la misma sesión HTTP;
- envía como `Referer` la página intermedia de exportación;
- descarga el XML adjunto;
- continúa con la selección de página, ALTO XML e imagen;
- mantiene intacta la búsqueda OCR y la deduplicación anteriores.

## Prueba

Se añadió una prueba basada en la estructura HTML real aportada desde Galiciana: visor → `mets.do` → botón `export-mets.do` → METS → ALTO de la página seleccionada.

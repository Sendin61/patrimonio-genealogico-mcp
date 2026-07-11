# Rob v0.5.2 — lectura METS/ALTO de Galiciana

Esta revisión mantiene la búsqueda OCR ya verificada y amplía `leer_pagina_galiciana` para:

- localizar el enlace «Descargar formato METS» del visor;
- descargar y analizar el METS;
- relacionar la imagen solicitada con su página física;
- localizar el fichero OCR ALTO XML de esa página;
- devolver `texto_ocr`, `ocr_url`, `imagen_pagina` y un resumen del METS;
- conservar diagnósticos y enlaces cuando Galiciana no exponga el ALTO.

## Instalación

Subir el contenido de este ZIP a la raíz del repositorio, reemplazando los archivos existentes.

Mensaje recomendado:

`Add Galiciana METS ALTO page reader`

## Prueba

Ejecutar `leer_pagina_galiciana` con:

`https://biblioteca.galiciana.gal/es/catalogo_imagenes/grupo.do?path=1356612&idImagen=13275824`

El resultado esperado debe incluir `lectura_completa: true` y texto en `texto_ocr`. Si devuelve `partial`, revisar `mets_url`, `acciones` y `errores_recuperacion`.

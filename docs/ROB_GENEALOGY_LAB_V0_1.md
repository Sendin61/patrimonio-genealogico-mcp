# ROB Genealogy Lab — arquitectura v0.1

## Objetivo

Aplicación local privada de investigación genealógica, accesible desde Chrome, que acepta lenguaje natural libre, mantiene expedientes persistentes y usa conectores documentales (FamilySearch, Galiciana, Europeana y futuras fuentes) bajo un motor común.

## Regla de instalación local

En Windows, todos los datos y componentes propios de la aplicación FamilySearch/ROB deben vivir bajo una única raíz visible en Descargas:

`%USERPROFILE%\Downloads\ROB-Genealogy-Lab`

Subdirectorios previstos:

- `app/`: interfaz y backend local.
- `extension/`: puente de Chrome para FamilySearch.
- `data/`: SQLite, índices y expedientes.
- `cache/`: respuestas y OCR reutilizables.
- `logs/`: diagnósticos técnicos.
- `config/`: configuración sin secretos versionados.

La aplicación no debe dispersar deliberadamente sus datos de usuario por Documentos, AppData u otras carpetas. Los datos internos inevitables de Chrome/Python quedan fuera de esta regla.

## Arquitectura

```text
Usuario en Chrome
      |
      v
Interfaz local (chat + expediente)
      |
      v
Orquestador de investigación
      |
      +-- Intérprete de intención
      +-- Planificador adaptativo
      +-- Scoring determinista de evidencia
      +-- Resolución de identidades
      +-- Contextual OCR Resolver
      |
      +-- FamilySearch Browser Bridge
      +-- Galiciana
      +-- Europeana
      +-- futuras fuentes
      |
      v
Base canónica + evidencia + hipótesis
```

## FamilySearch Browser Bridge

FamilySearch se consulta desde la sesión normal del usuario en Chrome. El puente no debe:

- eludir autenticación, CAPTCHA, controles anti-bot o límites de acceso;
- exportar cookies o credenciales;
- camuflar automatización para evitar un bloqueo;
- usar una sesión distinta de la que el usuario puede usar normalmente.

El puente sí puede ejecutar operaciones explícitas y controladas dentro de una pestaña FamilySearch ya autenticada y devolver al backend local las respuestas observables para esa sesión.

## Modelo de evidencia OCR

Nunca se sobrescribe el OCR original con una interpretación.

Cada lectura dudosa debe poder conservar al menos:

- `raw_ocr`: texto recibido de la fuente;
- `resolved_text`: candidato contextual, si existe;
- `confidence`: confianza de la resolución;
- `resolution_evidence`: factores que sostienen la resolución;
- `image_region`: coordenadas, cuando existan;
- `status`: observado / inferido / confirmado / rechazado.

Ejemplo conceptual:

```json
{
  "raw_ocr": "Bea",
  "resolved_text": "Varela",
  "confidence": 0.91,
  "status": "inferred",
  "resolution_evidence": [
    "parent-child formula",
    "known family co-occurrence",
    "same-document surname recurrence",
    "compatible chronology",
    "visual crop pending"
  ]
}
```

## Contexto documental multipágina — requisito obligatorio

Una coincidencia no se interpreta aislando únicamente la página donde aparece el término.

Para todo candidato que supere el umbral de interés, el motor debe construir un `DocumentContext` que incluya:

1. página central;
2. páginas vecinas iniciales;
3. OCR completo por página;
4. OCR estructurado/tokenizado cuando esté disponible;
5. señales de continuidad documental;
6. límites estimados del documento;
7. entidades y relaciones detectadas;
8. regiones de imagen relevantes.

La ventana inicial puede ser, por ejemplo, ±3 imágenes, pero no es una regla rígida. El motor debe poder ampliarla hacia atrás o adelante cuando haya señales de continuación y detenerse cuando detecte un nuevo documento.

## Separación epistemológica

La base distingue siempre:

- **evidencia observada**: lo que dice OCR, API o imagen;
- **interpretación**: lectura o relación propuesta por el sistema;
- **hecho aceptado**: afirmación que ha superado reglas de evidencia;
- **hipótesis**: explicación provisional pendiente de más pruebas.

Una IA nunca puede transformar por sí sola una interpretación en evidencia observada.

## Fluidez

La interfaz no se bloquea durante la investigación. El backend trabaja de forma asíncrona y publica progreso incremental.

Jerarquía de coste:

1. búsqueda masiva y filtros locales baratos;
2. OCR estructurado para candidatos prometedores;
3. contexto multipágina;
4. imagen/visión solo para dudas de alto valor.

Toda página, ARK, DGS, persona, búsqueda u OCR ya obtenido debe poder reutilizarse desde caché para evitar trabajo repetido.

## IA

La IA actúa como orquestador de investigación mediante herramientas estructuradas. Se separan al menos tres responsabilidades lógicas:

- interpretación de la petición del usuario;
- planificación de búsquedas y acciones;
- análisis de evidencia y formulación de hipótesis.

La aplicación conserva el expediente y solo envía al modelo el contexto pertinente para la tarea actual.

## Primer hito ejecutable

La primera versión aceptable debe permitir:

1. doble clic en un lanzador dentro de `Downloads\ROB-Genealogy-Lab`;
2. apertura automática de `http://127.0.0.1:8877` en Chrome;
3. crear/reabrir un expediente;
4. escribir una petición libre;
5. convertirla en un objetivo estructurado;
6. comprobar el estado del puente FamilySearch;
7. ejecutar una búsqueda controlada;
8. guardar los resultados y OCR en SQLite;
9. abrir un candidato y cargar contexto multipágina;
10. mostrar claramente evidencia e hipótesis sin mezclarlas.

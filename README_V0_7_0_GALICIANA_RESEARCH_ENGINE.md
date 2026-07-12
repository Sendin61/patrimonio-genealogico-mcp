# ROB v0.7.0 — Expedientes genealógicos reanudables de Galiciana

## Qué cambia

Esta versión deja de intentar buscar y leer decenas de páginas completas dentro de una sola llamada.

El trabajo se divide en cuatro acciones:

1. `crearInvestigacionGaliciana`
   - busca variantes del nombre dentro del OCR real de Galiciana;
   - filtra fechas;
   - elimina duplicados;
   - crea un expediente con `investigation_id`.

2. `procesarInvestigacionGaliciana`
   - lee las páginas pendientes por lotes pequeños;
   - mantiene una única sesión HTTP durante el lote;
   - accede directamente al ALTO XML mediante `path` y `posicion`;
   - usa caché SQLite;
   - reintenta fallos;
   - conserva el estado para continuar en la siguiente llamada;
   - consulta páginas contiguas solo cuando detecta una posible continuación.

3. `obtenerInformeGaliciana`
   - devuelve cobertura real;
   - separa páginas leídas y no leídas;
   - entrega contexto procedente del ALTO completo;
   - conserva enlaces a página, imagen y OCR;
   - pagina la respuesta para no saturar ChatGPT.

4. `crearInvestigacionFamiliarGaliciana`
   - solo permite abrir una investigación para una persona que aparezca mediante un parentesco explícito en una página ya leída;
   - no considera familiar a alguien por compartir apellido.

## Precisión documental

ROB conserva:

- texto OCR literal;
- geometría ALTO por línea: bloque, coordenadas, ancho y alto;
- contexto reconstruido alrededor del nombre;
- señales de posible continuación anterior o posterior;
- relaciones familiares explícitas y su frase probatoria.

La segmentación de artículos sigue siendo heurística. ROB no debe afirmar que ha aislado un artículo perfecto cuando la maquetación no lo permite.

## Persistencia

Por defecto se utiliza:

```text
/tmp/rob_galiciana.sqlite3
```

Esto mantiene el expediente durante la vida del servicio y evita repetir páginas. Render puede borrar ese archivo tras un reinicio o nuevo despliegue. Para persistencia durable puede configurarse más adelante `ROB_DB_PATH` sobre un disco persistente. No es necesario para la primera prueba.

## Archivos del parche

```text
server.py
rob/galiciana_investigations.py
tests/test_galiciana_investigations.py
GPT_INSTRUCCIONES_V0_7.txt
```

## Validación local

- compilación de `server.py`: correcta;
- compilación del nuevo motor: correcta;
- 4 pruebas específicas superadas;
- se prueba lectura ALTO directa, geometría, contexto, caché y parentesco explícito.

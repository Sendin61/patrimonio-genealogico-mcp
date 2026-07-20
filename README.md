# Patrimonio Genealógico MCP

Servidor MCP para búsquedas genealógicas en bibliotecas, archivos y hemerotecas digitales.

## Versión 0.8.0

Incluye:

- `estado`
- `buscar_europeana`
- `buscar_persona`
- `abrir_registro_europeana`

La clave de Europeana debe guardarse como variable de entorno y nunca subirse a GitHub.

## Autenticación de rutas operativas

Configura `ROB_ACTION_KEY` como variable secreta en Render (`sync: false`). Los clientes
deben enviarla en el encabezado `X-ROB-Key` al llamar a `/api/*` o `/mcp`. Las rutas
`/health`, `/privacy` y `/openapi.json` permanecen públicas. No guardes la clave en el
repositorio ni la incluyas en URLs.

## Persistencia de investigaciones

El servicio usa PostgreSQL automáticamente cuando `DATABASE_URL` está definida. En
Render, configura esa variable con la cadena de conexión interna proporcionada por
Neon; el valor no debe guardarse en el repositorio. Al arrancar, el servicio crea las
tablas e índices necesarios. Sin `DATABASE_URL`, usa SQLite en `ROB_DB_PATH` (o
`/tmp/rob_galiciana.sqlite3`), que es la alternativa prevista para desarrollo y tests.
Los expedientes no caducan de forma predeterminada. Se puede activar una poda explícita
definiendo `ROB_INVESTIGATION_TTL_DAYS` con un número de días mayor que cero.

El backend activo se puede comprobar en el campo `persistencia.backend` de `/health`
o en `almacenamiento.backend` de la herramienta `estado`.

## Motor universal de investigaciones

Un **expediente universal** es el identificador estable que coordina una investigación
completa. Cada fuente mantiene además su propia **investigación hija**, con un
identificador interno independiente para conservar la trazabilidad. Galiciana es la
primera fuente adaptada. Los `investigation_id` creados por las rutas antiguas de
Galiciana siguen siendo válidos y no se migran ni se copian.

Las rutas generales son:

- `POST /api/investigacion/crear`
- `POST /api/investigacion/procesar`
- `POST /api/investigacion/informe`
- `POST /api/investigacion/leer-fuente`

Todas requieren `X-ROB-Key`. Ejemplo mínimo:

```bash
curl -X POST "$ROB_URL/api/investigacion/crear" \
  -H "Content-Type: application/json" -H "X-ROB-Key: $ROB_ACTION_KEY" \
  -d '{"nombre":"Nombre Apellidos","fuentes":["galiciana"]}'

curl -X POST "$ROB_URL/api/investigacion/procesar" \
  -H "Content-Type: application/json" -H "X-ROB-Key: $ROB_ACTION_KEY" \
  -d '{"investigation_id":"ID_UNIVERSAL","elementos_por_lote":5}'

curl -X POST "$ROB_URL/api/investigacion/informe" \
  -H "Content-Type: application/json" -H "X-ROB-Key: $ROB_ACTION_KEY" \
  -d '{"investigation_id":"ID_UNIVERSAL","max_resultados":20}'
```

Exa, Firecrawl, FamilySearch y otras fuentes se incorporarán en fases posteriores
mediante nuevos adaptadores; esta versión no realiza llamadas a esos servicios.

## Próximas fuentes

Hispana, BNE, Hemeroteca Digital, Galiciana y portales autonómicos.

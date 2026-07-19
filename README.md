# Patrimonio Genealógico MCP

Servidor MCP para búsquedas genealógicas en bibliotecas, archivos y hemerotecas digitales.

## Versión 0.1

Incluye:

- `estado`
- `buscar_europeana`
- `buscar_persona`
- `abrir_registro_europeana`

La clave de Europeana debe guardarse como variable de entorno y nunca subirse a GitHub.

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

## Próximas fuentes

Hispana, BNE, Hemeroteca Digital, Galiciana y portales autonómicos.

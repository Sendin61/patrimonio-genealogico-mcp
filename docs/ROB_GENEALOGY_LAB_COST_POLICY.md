# ROB Genealogy Lab — política de coste y dependencia de IA

## Requisito de producto

ROB Genealogy Lab debe poder funcionar de extremo a extremo sin exigir créditos de una API de IA de pago, sin bloquear una investigación porque el usuario haya agotado saldo y sin imponer al usuario la gestión manual de tokens.

## Arquitectura local-first

La ruta normal del producto usa componentes locales para las tareas de gran volumen:

1. interpretación básica y normalización de la petición;
2. generación y expansión determinista de consultas;
3. búsqueda, filtrado, deduplicación y ranking;
4. almacenamiento, FTS y recuperación de contexto;
5. procesamiento de OCR y resolución contextual preliminar;
6. construcción de contexto documental multipágina;
7. caché y reanudación de investigaciones.

Estas tareas no deben depender de una API de pago.

## OpenAI u otras IA remotas

Los modelos remotos son una capa opcional de alta inteligencia, no un requisito del motor. La aplicación debe ofrecer al menos:

- `local`: investigación sin llamadas pagadas a IA remota;
- `hybrid`: procesamiento local y llamadas remotas selectivas solo en casos de alto valor;
- `remote`: uso más intensivo de IA remota si el usuario lo decide.

La ausencia de una clave API o de saldo no debe impedir abrir la aplicación, continuar un expediente, buscar FamilySearch, recuperar OCR, construir documentos multipágina ni usar el conocimiento ya almacenado.

## Límite de contexto

Todo modelo tiene una ventana de contexto finita. El producto no expondrá este detalle como un límite manual al usuario. El motor debe resolver documentos e investigaciones extensas mediante:

- segmentación por documentos y páginas;
- recuperación selectiva de evidencia;
- memoria persistente del expediente;
- resúmenes derivados que nunca sustituyen la evidencia original;
- caché;
- relectura dirigida de los fragmentos originales cuando haga falta.

Por tanto, un expediente puede crecer indefinidamente en disco aunque ninguna inferencia individual procese todo el corpus simultáneamente.

## Contextual OCR Resolver

La resolución de OCR debe funcionar primero con señales locales y deterministas: contexto multipágina, entidades, parentescos, coocurrencias, corpus del expediente, léxico histórico, variantes aprendidas y OCR estructurado. Una IA remota o visión remota puede reforzar una hipótesis, pero no debe ser obligatoria para conservar y procesar los datos.

## Transparencia de coste

Si el usuario activa un proveedor de pago, la aplicación debe:

- mostrar claramente que ese modo puede generar coste;
- permitir desactivarlo en cualquier momento;
- ofrecer límites monetarios configurables por el usuario;
- preferir caché y entradas compactas para no repetir trabajo;
- no efectuar llamadas remotas ocultas o innecesarias.

## Regla de diseño

Nunca diseñar una función esencial de ROB Genealogy Lab de forma que solo pueda ejecutarse mientras exista saldo en una API externa de pago.

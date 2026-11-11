# Decisiones de arquitectura (ADRs)

## ADR-001 — PostgreSQL y un modelo dimensional simple

**Estado:** Aceptada  
**Fecha:** 2026-11-10

**Contexto:** El conjunto tiene cerca de 115 mil lecturas y necesita relaciones
consistentes entre clientes, fronteras, consumos y facturas.

**Opciones:** Spark/lakehouse distribuido; PostgreSQL con dimensiones y hechos;
archivos agregados solamente.

**Decisión:** PostgreSQL local con `core.dim_cliente`, `core.dim_frontera`,
`core.fact_consumo_horario` y `core.facturacion`.

**Trade-off:** PostgreSQL añade mantenimiento de esquema, pero permite
transacciones, claves y roles verificables. Spark sumaría costo y complejidad
operativa sin beneficio para este volumen. CSV como almacén no permite probar
los permisos ni la bitácora requeridos.

## ADR-002 — Normalizar a `TIMESTAMPTZ` de Colombia y `Decimal`

**Estado:** Aceptada  
**Fecha:** 2026-11-10

**Contexto:** Febrero llega en UTC, el resto es local, y febrero usa coma
decimal; el resultado contractual está en kWh y hora Colombia.

**Opciones:** Guardar el timestamp original como texto; almacenar todo en UTC y
convertir solo al presentar; normalizar a instante con zona y presentar en
Bogotá.

**Decisión:** Parsear la zona explícita, convertir a `America/Bogota`, conservar
el instante en `TIMESTAMPTZ` y usar `Decimal` en vez de `float`.

**Trade-off:** El reporte debe convertir la zona explícitamente para agrupar por
mes. Se evita mezclar horas locales con UTC o introducir errores de punto
flotante al sumar consumo.

## ADR-003 — Upsert con precedencia por versión y procedencia

**Estado:** Aceptada  
**Fecha:** 2026-11-10

**Contexto:** Los duplicados no siempre coinciden y abril trae lecturas
corregidas. Reprocesar no debe borrar el histórico ni duplicarlo.

**Opciones:** Borrar y reconstruir tablas; agregar todas las filas; upsert según
la precedencia definida.

**Decisión:** Una única clave por frontera/instante. Gana la versión más alta;
los empates usan nombre de archivo lexicográfico y número de fila. PostgreSQL
actualiza cuando la fila entrante es la ganadora por precedencia, incluso si es
la misma posición de origen con contenido corregido.

**Trade-off:** Los hechos existentes de archivos que se retiren no se borran
automáticamente. Una política de retención/retractación sería una decisión
separada, con auditoría y aprobación explícitas.

## ADR-004 — Parsear todo antes de una única transacción de carga

**Estado:** Aceptada  
**Fecha:** 2026-11-10

**Contexto:** Durante la evaluación puede aparecer un archivo nuevo o
estructuralmente corrupto. Un archivo corrupto no debe dejar meses previos en
un estado parcial.

**Opciones:** Cargar por archivo; ignorar errores de parser; leer y validar todo
antes de abrir la transacción, y rechazar la corrida ante corrupción
estructural.

**Decisión:** Descubrir por glob, parsear todos los archivos y preparar sus
registros antes de iniciar la transacción. Los errores de contenido de una fila
van a cuarentena; los errores estructurales abortan.

**Trade-off:** El conjunto completo ocupa memoria durante el parseo. Es
proporcional al tamaño de esta prueba; para datos mayores, se haría validación
por lotes con staging y publicación atómica por partición/mes.

## ADR-005 — Cifrado AES-GCM antes de insertar PII

**Estado:** Aceptada para evaluación local  
**Fecha:** 2026-11-10

**Contexto:** El verificador revisa la tabla como superusuario; cifrado de
disco no oculta valores en un dump SQL.

**Opciones:** TLS/TDE únicamente; pgcrypto con una llave SQL; cifrado de
aplicación con llave fuera de la base.

**Decisión:** Cifrar columnas PII con AES-256-GCM en Python, antes de insertarlas
en columnas `BYTEA`. La llave aleatoria vive en `.env`; el AAD ata cliente y
columna. El nonce derivado mantiene ciphertexts estables en reintentos.

**Trade-off:** El ciphertext determinista revela igualdad del mismo valor en el
mismo campo/cliente; no se permite consultar/join por PII. La llave local junto
al host del contenedor no es gestión de llaves de producción. Producción
requiere KMS/envelope encryption, rotación y separación de funciones.

## ADR-006 — Roles PostgreSQL y vista como frontera analítica

**Estado:** Aceptada  
**Fecha:** 2026-11-10

**Contexto:** Un filtro en la aplicación no protege contra consultas directas
con las credenciales del rol.

**Opciones:** Filtrar PII en cada query; otorgar tablas base; separar permisos
en roles y dar al analista solo una vista sin columnas restringidas.

**Decisión:** `rol_ingesta` accede a tablas operativas cifradas, `rol_analista`
solo a `analytics.vw_consumo_mensual` y `rol_auditor` solo a `core.audit_log`.
La bitácora tiene trigger que rechaza UPDATE/DELETE y permisos revocados.

**Trade-off:** Las vistas definer-rights requieren revisar cuidadosamente su
definición. La vista actual es una agregación estática sin PII ni SQL dinámico.

## ADR-007 — Consumidor finito y evaluación de huecos por rejilla

**Estado:** Aceptada  
**Fecha:** 2026-11-10

**Contexto:** El target `make stream` tiene que terminar en 300 segundos y la
entrada incluye eventos tardíos y slots ausentes.

**Opciones:** consumidor permanente; inferir huecos por timeout real; comparar
el conjunto finito con los 96 slots y medir el orden de llegada por separado.

**Decisión:** El consumidor registra offsets iniciales, publica el NDJSON con el
productor provisto y drena únicamente esa ejecución. La ausencia se calcula en
la rejilla y la retroactividad en el orden de eventos.

**Trade-off:** Este es un replay finito de evaluación, no un servicio streaming
24/7. Para producción se necesitarían ventanas, watermark, persistencia de
estado y política de reenvío de alertas.

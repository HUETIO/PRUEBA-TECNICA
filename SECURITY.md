# Controles de seguridad y privacidad

## Clasificación y minimización

`data_classification.yaml` contiene una etiqueta para cada columna de las tablas
del modelo. `src.batch` la lee en cada inicio, valida categorías y asegura que
toda columna del maestro marcada `Restringido-PII` esté dentro del conjunto
cifrado. Los campos PII no se proyectan a la vista analítica, logs ni salidas.

## Cifrado

| Activo | Ubicación | Protección | Llave |
|---|---|---|---|
| PII del cliente | `core.dim_cliente` (`BYTEA`) | AES-256-GCM de aplicación antes de insertar, AAD por `cliente_id` y columna. | 32 bytes aleatorios en `.env` ignorado por Git. |
| Observación libre | Antes de cifrar en la misma columna | Detección/enmascaramiento de correo, celular y documento; después AES-GCM. | Igual a la anterior. |
| Volumen PostgreSQL | Docker local | No se declara cifrado de disco por separado; la PII está cifrada en aplicación. | No aplica. |
| IaC S3 | `infra/main.tf` | SSE-KMS, versionado, bloqueo de acceso público y transporte TLS obligatorio. | `aws_kms_key.data`, rotación habilitada. |

El nonce derivado ofrece ciphertext repetible para el mismo par
cliente/campo/valor, para que el upsert sea idempotente. Eso filtra igualdad
dentro de ese contexto; no se usa el cifrado determinista para buscar ni unir
PII. No hay código de descifrado en el pipeline ni función SQL de descifrado.
La llave local junto a la aplicación es aceptable solo para la prueba; en
producción usar envelope encryption/KMS, separación de operador y datos,
rotación, recuperación controlada y auditoría de uso.

## Menor privilegio PostgreSQL

| Rol | Puede | No puede |
|---|---|---|
| `rol_ingesta` | Leer/insertar/actualizar tablas de datos (PII únicamente como ciphertext); insertar auditoría. | No posee llave SQL ni función de descifrado; no puede mutar/borrar auditoría. |
| `rol_analista` | `SELECT analytics.vw_consumo_mensual`. | Sin SELECT en tablas base, sin PII, sin DML. |
| `rol_auditor` | `SELECT core.audit_log`. | Sin acceso a hechos, maestros o facturación; sin mutaciones. |

Los `GRANT`/`REVOKE` están en `sql/schema.sql`; trigger `reject_audit_mutation`
rechaza UPDATE/DELETE incluso si se concede por error el privilegio. La bitácora
registra conteos de corrida, resultado, actor de servicio e intervalo temporal,
no valores de cliente.

## Gestión de secretos

`tools/bootstrap.py` genera `.env` local con credenciales aleatorias efímeras y
llave AES. El archivo tiene permisos restringidos donde el sistema operativo
lo permite y está en `.gitignore`. `entrega.yaml` replica solo credenciales
efímeras requeridas por el verificador; nunca reutilizarlas fuera de evaluación.
La configuración heredada con credenciales/llave/webhook de ejemplo se retiró
de `config/pipeline.yaml` y de Compose. No cargar `.env.example` como secreto:
es solo una referencia.

## Auditoría y logs

`core.audit_log` registra una fila por corrida. PostgreSQL revoca UPDATE,
DELETE, TRUNCATE, REFERENCES y TRIGGER a roles de aplicación y un trigger
rechaza UPDATE/DELETE. Los logs JSONL solo almacenan nombres de eventos,
conteos, duración, UUID y tipo de error; nunca exception payload o líneas
fuente. En producción, exportar logs a destino inmutable, restringir borrado y
sincronizar relojes.

## Dependencias

`requirements.txt` elimina versiones heredadas antiguas y limita cada
dependencia a versiones compatibles actuales. Verificación local:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip install pip-audit
.\.venv\Scripts\python.exe -m pip_audit -r requirements.txt
```

En la primera ejecución del escáner se detectaron 7 advisories asociados a
`cryptography 46.0.7`; el rango se subió a `cryptography>=50.0.2,<51` y el
escaneo posterior informó **No known vulnerabilities found**. `pip-audit` es
herramienta local opcional y no forma parte del runtime en `requirements.txt`.
La auditoría se limita a advisories conocidos para las versiones resueltas;
revisar también transitivas y repetirla en cada actualización. No usar
`pip install` fuera del entorno aislado para esta entrega.

## Verificación reproducible

```bash
make setup
make up
make run
make stream
make test
make verificar
```

`make verificar` solo prueba conexión y existencia de objetos; no certifica
cifrado, grants ni calidad. Para probar permisos, conectarse como cada rol e
intentar una consulta SELECT a `core.dim_cliente`, un INSERT como analista y un
UPDATE como auditor; las tres operaciones deben ser denegadas. Para revisar
cifrado de tabla, consultar `octet_length(razon_social)` como superusuario y
comparar que el ciphertext no contenga el valor de entrada.

## Brechas conocidas

La infraestructura local comparte host y llave, no tiene alta disponibilidad,
rotación automática, KMS local, TLS entre contenedores ni pipeline de respuesta
formal. La política IAM de `infra/` representa la separación de acceso a S3 y
logs, pero IAM no sustituye los roles PostgreSQL en un despliegue real.

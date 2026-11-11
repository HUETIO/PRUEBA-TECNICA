# Modelo de amenazas — plataforma local de consumo

## 1. Alcance y flujo

```text
Fuentes CSV/JSON/NDJSON (no confiables)
   | [frontera A: parser]
   +--> validación y normalización Python --> cuarentena sin payload
   |                                       |
   | [frontera B: secreto APP_ENCRYPTION_KEY]
   +--> cifrado de columnas PII ----------+--> PostgreSQL core (contenedor)
                                                  | rol_ingesta
                                                  +--> vista analytics --> rol_analista
                                                  +--> audit_log -------> rol_auditor

NDJSON --> productor Kafka --> Redpanda --> consumidor finito --> alertas.json
```

El contenedor, el proceso Python y el host local comparten una frontera de
confianza limitada. El bucket/KMS/IAM está descrito como IaC no desplegado.

## 2. Activos

| Activo | Ubicación | Clasificación | Impacto |
|---|---|---|---|
| NIT, correo, celular, dirección, representante y observaciones | Clientes fuente y `core.dim_cliente` cifrada | Restringido-PII | Suplantación, fraude y sanción de privacidad. |
| Consumo horario e identificación de frontera | Fuentes, PostgreSQL y reportes | Confidencial | Error de facturación, exposición de patrón comercial. |
| Llave AES y contraseñas DB | `.env` local | Secreto | Descifrado de dump y acceso a la base. |
| Registros de auditoría | `core.audit_log` | Interno | Pérdida de trazabilidad. |
| Imágenes/infraestructura cloud | Terraform no desplegado | Interno | Exposición o costo si se despliega incorrectamente. |

## 3. Amenazas STRIDE

| # | Componente | STRIDE | Amenaza concreta | Prob. | Impacto | Control | Estado | ISO/IEC 27001:2022 Anexo A |
|---|---|---|---|---|---|---|---|---|
| T1 | Maestro de clientes | Tampering | Un CSV contiene SQL o texto hostil que intenta alterar consultas o escapar del parser. | M | A | `csv.DictReader`, campos como parámetros, cifrado previo y pruebas de comillas/multilínea; no se usa el contenido en SQL dinámico. | Sí | A.8.25, A.8.28 |
| T2 | Archivo CSV | Denial of service | Un campo gigante consume memoria o un CSV corrupto deja el batch a medio publicar. | M | A | Límite de campo de 16 MiB, parser estricto y lectura completa antes de transacción; filas estructurales cortas abortan. | Parcial | A.8.6, A.8.25 |
| T3 | PostgreSQL/dump | Information disclosure | Un DBA o dump obtiene valores PII en claro. | M | A | AES-256-GCM antes de insertar, AAD por fila/campo, columna `BYTEA`; ciphertext no incluye valor literal. | Sí, llave compartida local | A.8.24, A.8.12 |
| T4 | `.env` / host | Information disclosure | Un atacante local copia llave AES o contraseñas generadas. | M | A | Aleatoriedad, `.gitignore`, permisos restringidos, credenciales efímeras y sin claves en logs/config versionada. | Parcial; host único | A.5.17, A.8.24 |
| T5 | Rol analista | Elevation of privilege | Analista accede a tabla base o PII por SQL directo. | M | A | Grants solo a vista analítica; sin DML ni privilegios base. | Sí; verificar con `make verificar` y pruebas de permisos | A.5.15, A.8.3 |
| T6 | Rol ingesta | Elevation of privilege | Proceso batch usa permisos de superusuario y modifica esquema o lee PII en claro. | M | A | Separación: superusuario solo inicializa; ejecución batch usa `rol_ingesta`, ciphertext y permisos acotados. | Parcial; la llave existe en proceso local | A.5.15, A.8.2 |
| T7 | Bitácora | Repudiation/Tampering | Operador altera o borra eventos para ocultar una corrida. | M | A | INSERT append-only, revocación UPDATE/DELETE/TRUNCATE y trigger; rol auditor read-only. | Parcial; superusuario del laboratorio puede administrar instancia | A.8.15, A.8.16 |
| T8 | Facturación | Tampering | Factura huérfana se une a la frontera equivocada o entra en analytics. | M | A | Validar frontera contra maestro, excluir del modelo y registrar motivo/hash en cuarentena. | Sí | A.8.25, A.8.26 |
| T9 | Dedupe batch | Tampering | Una lectura repetida o tardía reemplaza por error una medición superior. | M | A | Clave compuesta, versión mayor, desempate por nombre de archivo y fila; upsert condicionado. | Sí | A.8.25 |
| T10 | Logs y reportes | Information disclosure | La excepción imprime una fila que contiene correo/celular/NIT. | M | A | JSONL con tipo de error y conteos; nunca se loguea payload. Cuarentena conserva solo hash. | Sí; revisión de código, falta escaneo automatizado en build | A.8.12, A.8.15 |
| T11 | Redpanda | Spoofing/Tampering | Productor no autenticado introduce mediciones falsas al tópico local. | M | M | Tópico expuesto solo en loopback en entorno de prueba; offsets de ejecución y validación de frontera/valor. | Parcial; no hay SASL/TLS local | A.8.20, A.8.21 |
| T12 | Terraform/S3 | Information disclosure | Bucket público o carga sin cifrado/TLS. | B | A | Bloqueo de acceso público, SSE-KMS, versionado, política TLS y denegación de PutObject sin SSE-KMS. | Declarado en IaC, no desplegado | A.5.23, A.8.24, A.8.28 |

## 4. Riesgos aceptados

- En local, el superusuario puede cambiar roles, trigger, configuración o
  inspeccionar ciphertext. El objetivo es no guardar PII como texto plano, no
  resistir al administrador del host con acceso a la llave.
- La llave derivada para nonce hace que el mismo valor del mismo campo/cliente
  produzca igualdad observable. No se consulta PII por igualdad; una solución
  productiva requiere evaluación criptográfica, envelope encryption y
  versionado/rotación.
- Redpanda local no tiene autenticación TLS; la exposición está limitada a
  localhost y no debe usarse en red compartida.
- El pipeline mantiene en memoria el lote parseado antes de transaccionar. Para
  datos mucho mayores usar staging particionado, límites/streaming y publicación
  atómica.
- Los datos fuente se mantienen en texto plano porque son la entrada del
  ejercicio. El flujo no elimina ni modifica los archivos originales.

## 5. Con más tiempo / producción

Separar llave y workload con AWS KMS envelope encryption; secret manager y
rotación; TLS/SASL en Kafka/PostgreSQL; backups cifrados con prueba de restore;
auditoría exportada a almacenamiento inmutable; IAM y roles DB integrados con
identidad real; umbrales DQ acordados, alertas operativas y revisión legal bajo
Ley 1581. Agregar pruebas de integración que intenten explícitamente las
operaciones denegadas por rol.

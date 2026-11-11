# Plataforma local de datos — consumo y seguridad

Implementación de la prueba técnica Data Platform & Security Engineer. El
pipeline lee los archivos originales que se proporcionen localmente en `data/`,
conserva la procedencia, normaliza consumo horario a kWh y hora Colombia,
carga PostgreSQL de forma reintentable y produce informes batch y alertas de
streaming. Los archivos fuente se excluyen del repositorio porque contienen
datos personales y confidenciales; los datos fuente no se modifican.

> **Datos fuente:** no están incluidos en este repositorio porque pueden
> contener información personal o confidencial. No copies datos sensibles a
> Git, logs, capturas, tickets ni servicios externos.

## Clonar el repositorio

Requisitos previos: Git, Python 3.10 o posterior y Docker Desktop con Docker
Compose v2. GNU Make es necesario para los comandos `make`; Terraform 1.5+ es
opcional y solo se usa para validar la infraestructura.

```bash
git clone https://github.com/HUETIO/PRUEBA-TECNICA.git
cd PRUEBA-TECNICA
```

Los archivos de origen se excluyen deliberadamente. Si tienes autorización
para usarlos, colócalos localmente en `data/consumos/`, `data/eventos/`,
`data/facturacion/` y `data/maestros/`, según corresponda. Esa carpeta está
ignorada por Git y no se subirá al repositorio.

## 1. Qué requisitos cubre

| Requisito | Implementación | Verificación |
|---|---|---|
| A · Ingesta batch | Descubrimiento `data/consumos/*.csv`, detección de encoding/separador, normalización, modelo `core`, deduplicación por versión/archivo/fila y upsert transaccional. | `make run` y `out/consumo_mensual.csv` |
| A · Correcciones y robustez | Las lecturas corregidas ganan por `version_lectura`; un archivo estructuralmente truncado aborta antes de abrir la transacción de carga. Las filas inválidas quedan en cuarentena. | `core.cuarentena`, `out/dq_report.json` |
| B · Calidad | UTC a `America/Bogota`, kWh/MWh, nulos/negativos/outliers, duplicados, huecos horarios e invoices huérfanas. | `out/dq_report.json` |
| C · Seguridad | Clasificación viva, cifrado de columnas PII, roles PostgreSQL de mínimo privilegio, vista analítica sin PII, auditoría append-only, secretos locales efímeros y threat model. | `data_classification.yaml`, `sql/schema.sql`, `make verificar` |
| D · Streaming | Publica el NDJSON con `tools/producer.py`, drena el tópico y emite una sola alerta por secuencia de tres o más intervalos ausentes. | `make stream`, `out/alertas.json` |
| E · Operación | Logs JSON sin contenido de filas, métricas por corrida e instrucciones de recuperación. | `logs/pipeline.log`, `out/metrics.json`, `RUNBOOK.md` |
| F · IaC | Bucket S3 versionado/cifrado con KMS, política de transporte, roles IAM acotados y logs. No se despliega. | `cd infra; terraform validate` |

## 2. Requisitos locales y entorno aislado

- Python 3.10 o posterior.
- Docker Desktop con Docker Compose v2 para PostgreSQL y Redpanda.
- GNU Make para ejecutar el contrato `make`.
- Terraform 1.5+ solo si se quiere validar el IaC; **no** ejecutar `terraform apply`.

En Windows PowerShell, el entorno de este proyecto se creó en `.venv`. Para
repetirlo sin tocar el Python global:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

En Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

`make setup` realiza esos pasos de forma idempotente y usa exclusivamente el
intérprete de `.venv` para los demás targets. En Windows, `make` debe estar
instalado; si no, se pueden ejecutar los scripts con
`.\.venv\Scripts\python.exe -m src.batch` y
`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`.

La dependencia `tzdata` provee `America/Bogota` en Windows, donde Python no
siempre incluye una base de zonas horarias del sistema. FastAPI se mantiene
por debajo de 0.128 para conservar compatibilidad con el servidor web que usa
Flet. `requirements.txt` contiene solo librerías que usa el proyecto y elimina
paquetes heredados no utilizados. La exploración local con `pip-audit` encontró avisos para
`cryptography 46.0.7`; se actualizó el rango a `>=50.0.2,<51` y el segundo
escaneo no encontró vulnerabilidades conocidas.

## 3. Contrato de ejecución

Desde la raíz del proyecto:

```bash
make setup
make up
make run
make run
make stream
make front
make test
make verificar
make down
```

- `make up` crea `.env` con contraseñas aleatorias y una llave AES de 256 bits,
  inicia PostgreSQL/Redpanda y la API HTTP, y crea el esquema y los roles. No se
  fija ni se imprime ningún secreto. Si ya hay `.env`, se conserva; no elimines
  el volumen PostgreSQL mientras conservas credenciales diferentes.
- `make run` solo accede a PostgreSQL como `rol_ingesta`. Primero lee y valida
  **todos** los archivos; un error estructural aborta antes de cargar. En una
  transacción sube dimensiones, hechos, facturas, cuarentena y auditoría.
- `make stream` asegura el tópico `mediciones.raw`, consume el conjunto finito
  del archivo y sale; no deja un consumidor escuchando indefinidamente.
- `make front` abre el dashboard Flet en el navegador. Requiere que `make up`
  haya iniciado la API local y que los procesos `make run` y `make stream`
  hayan generado los datos y artefactos que se muestran.
- `make test` corre `unittest` y no necesita Docker/PostgreSQL.
- `make verificar` requiere infraestructura arriba y verifica las credenciales
  y nombres declarados en `entrega.yaml`.
- `make down` detiene contenedores sin borrar datos. `make clean` elimina
  volúmenes y salidas generadas, pero conserva `.env`.

Para borrar también los secretos locales, elimina `.env` manualmente después
de detener la infraestructura. **No** empaquetes `.venv/`, `.env`, volúmenes
Docker ni los datos fuente.

## 3.1 Dashboard Flet y API local

El dashboard se ejecuta en el equipo del usuario y consulta por HTTP una API
de solo lectura que corre en Docker. La API se conecta a PostgreSQL únicamente
con `rol_analista` y expone la vista `analytics.vw_consumo_mensual`; no devuelve
columnas PII. Los puertos de API y Flet se enlazan a `127.0.0.1`, por lo que no
quedan publicados en la red local.

### Inicio guiado en Windows

Ejecuta `INICIAR_PRUEBA.bat` desde el Explorador de archivos o desde una
terminal. El menú permite consultar el resumen de la solución o iniciar el
software. Al iniciar, el archivo comprueba Python 3.10+ y Docker Compose v2,
intenta abrir Docker Desktop si está instalado pero detenido, crea `.venv`,
instala `requirements.txt`, prepara `.env`, inicia los servicios, carga los
datos y abre el dashboard. Se necesita conexión a internet para descargar
dependencias e imágenes de Docker. Los secretos locales se conservan en `.env`;
no compartas ese archivo ni lo incluyas en la entrega.

### Inicio y uso

En una terminal PowerShell, desde la raíz del proyecto:

```powershell
make setup
make up
make run
make stream
make front
```

El navegador abre el dashboard en `http://127.0.0.1:8550`. La API está
disponible en `http://127.0.0.1:8000` y su contrato interactivo en
`http://127.0.0.1:8000/docs`. Si `make front` no abre el navegador, visita la
URL manualmente. Para cerrar la infraestructura, ejecuta `make down`.

El usuario puede filtrar el consumo mensual por mes y frontera, revisar los
indicadores agregados, consultar métricas de calidad y ver las alertas de
streaming. El botón **Actualizar** vuelve a consultar la API. Si faltan los
artefactos de calidad o alertas, ejecuta `make run` o `make stream`,
respectivamente, y vuelve a actualizar. Para usar otro puerto de API antes
de iniciar Docker, configura `API_PORT` en `.env` y `DATA_API_URL` al ejecutar
el front, por ejemplo:

```powershell
$env:DATA_API_URL = "http://127.0.0.1:8001"
make front
```

La API ofrece estos endpoints:

| Endpoint | Datos |
|---|---|
| `GET /health` | Estado del proceso API. |
| `GET /api/catalogos` | Meses y fronteras disponibles para filtros. |
| `GET /api/indicadores` | Consumo total, fronteras, meses y agregados mensuales. |
| `GET /api/consumos?mes=2026-04&frontera_id=...` | Agregados analíticos filtrables; máximo 1.000 filas. |
| `GET /api/calidad` | `out/dq_report.json`. |
| `GET /api/metricas` | `out/metrics.json`. |
| `GET /api/alertas?limit=100` | `out/alertas.json`, limitado a 1.000 resultados. |

## 4. Batch, reglas de datos y modelo

### Normalización y precedencia

- CSV leído con `csv.DictReader`, comillas escapadas y campos multilinea; se
  admite UTF-8/UTF-8 BOM y Windows-1252. Los campos grandes se limitan a 16 MiB
  para evitar abuso de memoria.
- `timestamp_utc` se interpreta como UTC y se convierte a `America/Bogota`.
  Un timestamp sin zona se entiende local Colombia; en PostgreSQL se almacena
  como `TIMESTAMPTZ`.
- Los valores con coma decimal se convierten con `Decimal`. `MWh` se multiplica
  por 1.000. El destino y la regla de máximo están en kWh.
- Para cada `(frontera_id, timestamp)`, gana la mayor versión; el empate lo
  resuelven el nombre de archivo lexicográfico y, por último, el número de fila.
- Filas vacías, no numéricas, negativas, superiores a 1.000.000 kWh, con unidad
  no soportada o sin frontera de maestro se rechazan con motivo y hash SHA-256.
  No se conserva el payload crudo en cuarentena.
- El reporte de huecos calcula cada hora de cada frontera del maestro para
  cada mes encontrado, incluyendo horas que tenían fila inválida.
- El parser de facturación recorre la estructura `facturas`; las fronteras
  inexistentes se reportan y no se insertan.
- Los maestros se cargan antes de los hechos, y la validación completa de
  todos los archivos ocurre antes de abrir la transacción. `ON CONFLICT` aplica
  upsert solamente si la versión/procedencia entrante tiene mayor precedencia.

### Tablas y artefactos

| Objeto | Uso |
|---|---|
| `core.dim_cliente` | Maestro de clientes. Todas las columnas PII son bytes cifrados. |
| `core.dim_frontera` | Frontera, cliente y límites operativos. |
| `core.fact_consumo_horario` | Lectura ganadora por frontera y hora local. |
| `core.facturacion` | Totales de facturas válidas sin copiar los datos de cliente anidados. |
| `core.cuarentena` | Archivo/fila, motivo y hash; clave única para reintentos. |
| `core.audit_log` | Conteos por corrida; trigger y privilegios bloquean mutaciones. |
| `analytics.vw_consumo_mensual` | Agregación sin columnas PII para analistas. |

`out/consumo_mensual.csv` tiene las columnas y orden exigidos:
`frontera_id,mes,consumo_kwh,horas_con_dato`. `out/dq_report.json` contiene
todas las claves obligatorias del contrato. `out/metrics.json` añade lectura,
rechazo por motivo, carga, duración por etapa, timestamp y archivos/meses.
`logs/pipeline.log` es JSONL y no almacena filas, nombres, correos, valores de
factura ni excepciones con payload.

La idempotencia se aplica a hechos, dimensiones, facturas y cuarentena. Los
resultados de negocio no se duplican ni cambia su precedencia entre corridas.
El registro de auditoría es deliberadamente append-only y cada corrida añade
una fila; timestamps/duraciones de métricas y logs también cambian por diseño.
En `metrics.json`, `filas_leidas` agrega consumo, factura y maestros;
`filas_consumo_leidas` coincide con la clave contractual de DQ.

## 5. PII, roles y secretos

`data_classification.yaml` etiqueta todas las columnas del modelo; el pipeline
la carga y valida al inicio, y falla si las columnas cifradas del cliente dejan
de estar en `Restringido-PII`.

Los campos `razon_social`, `nit`, `email_contacto`, `celular_contacto`,
`direccion`, `representante_legal` y `observaciones` se cifran con AES-256-GCM
antes de insertarse. Cada cifrado usa AAD asociado a cliente/campo y un nonce
derivado por HMAC de ese mismo valor para que reintentos idénticos no reescriban
el estado. Esto revela igualdad solo dentro del mismo cliente/campo; no se
ofrece búsqueda por igualdad de PII. La llave maestra se genera localmente y
vive en `.env`, nunca en el código ni en el YAML de pipeline. No es un KMS
productivo ni un esquema de rotación: es una llave local de evaluación.

En `observaciones` se detectan y enmascaran patrones de correo, celular y
documento antes de cifrar. El conteo agregado aparece en DQ y no se exporta el
texto libre. El analista solo recibe la vista agregada. La ingesta ve los
ciphertexts y puede insertar/actualizar tablas de datos; no existe función de
descifrado en PostgreSQL. Auditoría solo permite `SELECT` sobre la bitácora.

| Rol | Concede | No concede |
|---|---|---|
| `rol_ingesta` | SELECT/INSERT/UPDATE en las tablas operativas cifradas y append a auditoría. | Lectura de PII en claro, DELETE de auditoría, acceso a la vista como rol analítico. |
| `rol_analista` | SELECT sobre `analytics.vw_consumo_mensual`. | Tablas base, PII y escritura. |
| `rol_auditor` | SELECT sobre `core.audit_log`. | Lectura de clientes/consumos/facturación y mutaciones. |

Las contraseñas evaluables se generan al ejecutar `make up` y se copian a
`entrega.yaml` porque el verificador necesita conectarse. Son solo para esta
instalación local; no reutilizarlas. La configuración heredada no confiable fue
retirada de `config/pipeline.yaml` y Docker Compose; no se usa el servicio
MinIO en este diseño.

## 6. Streaming

El consumidor fija un inicio de offsets antes de llamar al productor y drena
solo los mensajes de esa ejecución. No confunde una segunda corrida con los
eventos anteriores retenidos en Redpanda. Se procesan los 96 intervalos
esperados, de `2026-06-01T00:00:00-05:00` a `23:45`.

- `CONSUMO_FUERA_DE_BANDA`: comparación estricta `kwh > max_kwh`.
- `LECTURA_RETROACTIVA`: timestamp menor que el evento previamente procesado
  para esa frontera.
- `FRONTERA_SIN_REPORTE`: un intervalo de ausencia de longitud tres o mayor
  produce una sola alerta en el timestamp del tercer slot ausente; otra caída
  después de una lectura produce otra alerta.

Las alertas se deduplican por tipo/frontera/timestamp y se ordenan de forma
estable. El JSON contiene exclusivamente `tipo`, `frontera_id` y `ts`.

## 7. Pruebas y estado de validación

La suite cubre normalización UTC, decimales/unidades, duplicados y correcciones,
rechazos/huecos, parseo de CSV hostil (Windows-1252, comillas, multilinea y
campos grandes), rechazo de CSV truncado, cifrado/enmascaramiento y las tres
reglas de alertas. Cuando los datos fuente locales están disponibles, se
ejecutan pruebas de perfil para detectar regresiones en su formato.

```bash
make test
```

La prueba unitaria no depende de Docker. Para validar roles y base real, ejecutar
el contrato completo y finalizar con `make verificar`. Para IaC:

```bash
cd infra
terraform init
terraform validate
```

No ejecutar `terraform apply`: el diseño cloud es solo artefacto de lectura.

## 8. Arquitectura y riesgos aceptados

La solución es un pipeline Python acotado y Postgres local, no Spark/Airflow:
el volumen previsto para esta prueba cabe holgadamente en un proceso local y no
justifica costos/operación distribuida. La ingesta en una transacción reduce
complejidad de recuperación y evita que un archivo inválido deje una carga
parcial. La evidencia de decisiones técnicas está en `DECISIONS.md`.

Riesgos locales deliberados: la llave AES está en el mismo host que el
contenedor PostgreSQL, no hay rotación de llaves ni catálogo externo de secretos,
y el entorno Compose no es alta disponibilidad. `THREAT_MODEL.md` describe
controles y brechas sin presentarlas como seguridad de producción.

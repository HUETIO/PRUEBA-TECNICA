# Runbook operativo

## Operación normal

En local, desde la raíz, `make up` levanta PostgreSQL/Redpanda y aplica DDL;
`make run` carga todos los consumos, maestros y facturas; `make stream` publica
y consume el conjunto finito de mediciones; `make down` detiene contenedores
sin borrar datos. Batch y stream deben terminar en segundos o pocos minutos.
Cada corrida produce logs JSONL y reportes versionables por ejecución en `out/`.

## Señales y diagnóstico

| Síntoma | Revisar | Causa probable / acción |
|---|---|---|
| `make run` no conecta | `docker compose ps`, `.env` y puerto PostgreSQL | Esperar healthcheck; no cambiar el secreto de un volumen ya inicializado. |
| Batch termina con `SourceFormatError` | Nombre de archivo y esquema, sin imprimir filas | Encabezado roto, encoding desconocido, comillas incompletas o fila truncada. No se toca la BD; corregir el archivo fuente o aislarlo y reintentar. |
| Sube `filas_rechazadas` | `out/dq_report.json`, conteos por motivo en metrics y `core.cuarentena` | Validar la fuente con el dueño de datos; no imputar automáticamente. |
| Suben las horas faltantes | `horas_sin_lectura_valida_por_frontera` | Dato ausente o inválido. Comparar contra origen y facturación antes de cerrar mes. |
| `make stream` no termina | `docker compose logs redpanda`, logs JSON de la corrida | Tópico/cluster inaccesible o menos offsets consumidos que publicados. Reintentar una vez restablecido el broker. |
| Verificador falla en roles | `entrega.yaml` frente a `.env`, grants en `sql/schema.sql` | Se recreó el volumen, cambió credencial o difiere un nombre de objeto/puerto. |
| Sospecha de exposición PII | Limitar acceso a host, revisar `logs/` y permisos DB | No copiar logs/dumps a canales públicos; rotar llave y credenciales, activar respuesta de privacidad. |

## Procedimientos

### Fallo a mitad de corrida

El parseo batch ocurre antes de abrir una transacción; dimensiones, hechos,
facturas, cuarentena y auditoría se confirman juntos. Ante error de conexión o
SQL, PostgreSQL revierte la transacción. Revisar el evento `batch_failed` (solo
tipo de error, sin datos de fila) y reintentar con `make run` una vez recuperada
la dependencia. Upsert y claves únicas hacen seguro el reintento.

Si un archivo entero está corrupto, no lo borres del repositorio ni lo edites
sin autorización. Conserva copia controlada, pide reenvío al productor, verifica
encoding/encabezado y repite. No se permite cargar parcialmente un archivo cuya
estructura no se puede interpretar con confianza.

### Lecturas corregidas de un mes cerrado

Depositar el archivo corregido en `data/consumos/` con un nombre que preserve el
orden lexicográfico acordado. `version_lectura` mayor gana; a igual versión,
gana el archivo posterior y después la última fila. Ejecutar `make run`, revisar
`correcciones_tardias_aplicadas`, agregados mensuales y facturación antes de
cerrar. No borrar ni recrear tablas para corregir un registro.

### Tasa de rechazo alta

Pausar publicación a consumidores, comparar conteos por motivo con el umbral
acordado (este paquete no define un SLA), revisar esquema, unidades, valores y
hora de origen. Reanudar solo después de comprobar datos con el responsable de
la fuente. No rellenar huecos ni convertir errores desconocidos a cero.

### Sospecha de acceso indebido a PII: primeros 15 minutos

1. Aislar el host/servicio y revocar credenciales afectadas; no borrar evidencia.
2. Preservar hora, logs estructurados, eventos de acceso y hashes, evitando
   copiar payloads a un ticket o chat.
3. Notificar al responsable de seguridad y privacidad por el canal interno.
4. Identificar qué columnas/cuentas se expusieron, alcance temporal y copias.
5. No descifrar, modificar o compartir datos fuera del proceso de respuesta.
6. En entorno real, seguir el playbook legal/notificación de incidentes y Ley
   1581; este laboratorio no reemplaza ese procedimiento.

## Escalamiento

Asignar a: dueño de plataforma (infraestructura y restore), dueño de datos
(fuente y corrección), seguridad/privacidad (incidente PII), responsable
funcional de facturación (impacto de lecturas). Los nombres y teléfonos deben
completarse en el runbook operativo real; no inventarlos en esta entrega.

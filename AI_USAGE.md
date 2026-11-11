# Uso de IA en esta prueba

## 1. Herramienta y alcance

| Herramienta | Uso | Alcance aproximado |
|---|---|---|
| Asistente de IA (Copilot SDK en VS Code) | Lectura guiada del enunciado y scaffolding; diseño/implementación de parser, batch, stream, SQL, IaC, documentación y pruebas. | Mayoría del código nuevo; la persona que entrega debe revisar y poder explicar cada decisión. |
| Python 3.13 en `.venv` | Perfil local de CSV/JSON/NDJSON y ejecución de tests; no es IA. | Validación reproducible de reglas. |

## 2. Prompts que orientaron el trabajo

```text
lee todo el proyecto y completa todos los requisitos, adicoinalmente realiza pruebas unitarias, he leido el test y al parecer es un tema de big data con csv, existen dos requirementes un readme o quizas ya este cambiado el repo, actualmente no tengo ninguna libreria, pero para que no nos vallamos a confundir con las variables del sistema por favor crea un ebntorno virtual python para esto, deja un readme muy especifico acerca de lo que requieren
```

**Resultado:** se leyó el contrato, se creó `.venv`, se perfilaron fuentes,
se implementó el pipeline y se documentó su uso. Se corrigieron errores usando
pruebas locales.

```text
Recuerda esres un big data experto y tu eres el que realizará las experimentaciones, además debes indicar las mejores mdetodologias de explicacion al usuario para que este pueda responder con claridad, cuando termines indicame.
```

**Resultado:** se experimentó con las fuentes dentro del entorno aislado y se
añadió al README una estructura de respuesta basada en evidencia, trade-offs y
verificación, junto a STAR para preguntas conductuales.

## 3. Propuesta de IA que se cambió

| Archivo / función | Primer resultado | Cambio y motivo |
|---|---|---|
| `tests/test_pipeline.py`, fixture CSV hostil | La aserción inicial supuso que `csv` devolvía `\n` dentro de un campo citado de un archivo escrito en Windows. | El test mostró que el texto físico conservaba `\r\n`; la expectativa se cambió para representar el comportamiento real del parser, no normalizar newline que el contrato no pide cambiar. |
| Batch de PII | Una llave generada aleatoriamente por campo haría ciphertext distinto cada corrida. | Se derivó nonce con HMAC/AAD para preservar idempotencia; se documentó la fuga limitada de igualdad como trade-off y no se usa para consultar PII. |

## 4. Error de IA encontrado y corregido

En `tests/test_pipeline.py`, el test de CSV hostil calculó mal la longitud del
campo esperado y luego asumió newline LF. `unittest` lo detectó: el parser
preservó CRLF dentro de las comillas. Se sustituyó esa aproximación por una
comparación del texto completo con CRLF explícito. La suite quedó pasando.

## 5. Qué no debe delegarse sin revisión

Interpretación de las reglas de precedencia, definición de dato personal y
aprobación del riesgo criptográfico requieren juicio humano y revisión del
responsable de seguridad. Las cifras del perfil proceden de correr el código
localmente; no son promesas de operación en una infraestructura que no se
ejecutó. La persona candidata debe reemplazar o complementar este archivo con
sus propias reflexiones y no presentar la generación automática como trabajo
manual.

@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Plataforma local de datos - Prueba tecnica
cd /d "%~dp0"

:MENU
cls
echo ============================================================
echo   PLATAFORMA LOCAL DE DATOS - PRUEBA TECNICA
echo ============================================================
echo.
echo   1. Preparar el entorno e iniciar el dashboard
echo   2. Ver detalles de la solucion
echo   3. Salir
echo.
choice /C 123 /N /M "Selecciona una opcion: "
if errorlevel 3 exit /b 0
if errorlevel 2 goto DETAILS
if errorlevel 1 goto START

:DETAILS
cls
echo ============================================================
echo   DETALLES DE LA SOLUCION
echo ============================================================
echo.
echo   ^- Ingesta batch de consumos, maestros y facturas.
echo   ^- Normalizacion horaria y de unidades, validaciones de calidad
echo     y registro de filas rechazadas sin conservar sus datos crudos.
echo   ^- PostgreSQL con roles de minimo privilegio y cifrado de PII.
echo   ^- Streaming de mediciones y generacion de alertas.
echo   ^- API local de solo lectura y dashboard Flet con filtros,
echo     indicadores, calidad de datos y alertas.
echo   ^- Docker Compose inicia PostgreSQL, Redpanda y la API.
echo.
echo   El dashboard se abre en http://127.0.0.1:8550
echo   La API local responde en http://127.0.0.1:8000
echo.
echo   Requisitos: Windows, Python 3.10 o posterior, Docker Desktop
echo   iniciado y conexion a internet para instalar paquetes/imagenes.
echo.
pause
goto MENU

:START
cls
echo ============================================================
echo   PREPARANDO E INICIANDO EL DASHBOARD
echo ============================================================
echo.

set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)
if defined PYTHON_CMD goto PYTHON_READY

where python >nul 2>&1
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)
if defined PYTHON_CMD goto PYTHON_READY

where python3 >nul 2>&1
if not errorlevel 1 (
    python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python3"
)
if defined PYTHON_CMD goto PYTHON_READY

echo [ERROR] No se encontro Python 3.10 o posterior.
echo Instala Python desde https://www.python.org/downloads/
echo y marca "Add python.exe to PATH". Despues vuelve a ejecutar este archivo.
goto PAUSE_EXIT

:PYTHON_READY
echo [OK] Python disponible: %PYTHON_CMD%

where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker no esta instalado o no esta disponible en PATH.
    echo Instala Docker Desktop desde https://www.docker.com/products/docker-desktop/
    goto PAUSE_EXIT
)

docker compose version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Se requiere Docker Compose v2.
    echo Actualiza o instala Docker Desktop y vuelve a intentarlo.
    goto PAUSE_EXIT
)
echo [OK] Docker y Docker Compose v2 estan instalados.

docker info >nul 2>&1
if errorlevel 1 (
    if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
        echo Docker Desktop esta detenido. Intentando iniciarlo...
        start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    ) else (
        echo [AVISO] Inicia Docker Desktop para continuar.
    )
    set "WAIT_COUNT=0"
    goto WAIT_DOCKER
)
goto DOCKER_READY

:WAIT_DOCKER
docker info >nul 2>&1
if not errorlevel 1 goto DOCKER_READY
if %WAIT_COUNT% geq 12 (
    echo [ERROR] Docker Desktop no respondio despues de 60 segundos.
    echo Abre Docker Desktop, espera a que indique que esta iniciado y reintenta.
    goto PAUSE_EXIT
)
timeout /t 5 /nobreak >nul
set /a WAIT_COUNT+=1
goto WAIT_DOCKER

:DOCKER_READY
echo [OK] El motor de Docker esta iniciado.
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual .venv...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] No se pudo crear el entorno virtual.
        goto PAUSE_EXIT
    )
)

".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] El entorno .venv existente usa Python anterior a 3.10.
    echo Renombra o elimina .venv y vuelve a ejecutar este archivo.
    goto PAUSE_EXIT
)
echo [OK] Entorno virtual .venv listo.

echo.
echo Instalando o verificando librerias de requirements.txt...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] No se pudo actualizar pip. Verifica tu conexion a internet.
    goto PAUSE_EXIT
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] No se pudieron instalar las librerias requeridas.
    goto PAUSE_EXIT
)

echo.
echo Preparando credenciales locales...
".venv\Scripts\python.exe" tools\bootstrap.py
if errorlevel 1 (
    echo [ERROR] No se pudo preparar .env o sincronizar entrega.yaml.
    echo Revisa el mensaje anterior; no se muestran ni se imprimen secretos.
    goto PAUSE_EXIT
)

echo.
echo Iniciando PostgreSQL, Redpanda y la API con Docker Compose...
docker compose up -d --wait
if errorlevel 1 (
    echo [ERROR] Docker Compose no pudo iniciar la infraestructura.
    echo Revisa Docker Desktop y ejecuta "docker compose logs" para diagnostico.
    goto PAUSE_EXIT
)

echo.
echo Inicializando esquema y roles de base de datos...
".venv\Scripts\python.exe" -m src.database
if errorlevel 1 (
    echo [ERROR] No se pudo inicializar PostgreSQL.
    goto PAUSE_EXIT
)

echo.
echo Procesando los datos de prueba...
".venv\Scripts\python.exe" -m src.batch
if errorlevel 1 (
    echo [ERROR] La ingesta batch fallo. Revisa los logs locales para diagnostico.
    goto PAUSE_EXIT
)

echo.
echo Procesando el flujo de mediciones y las alertas...
".venv\Scripts\python.exe" -m src.stream
if errorlevel 1 (
    echo [ERROR] El proceso de streaming fallo. Revisa Docker y los logs locales.
    goto PAUSE_EXIT
)

echo.
echo [OK] Preparacion completa. Abriendo el dashboard en el navegador.
echo Para cerrar el dashboard, cierra esta ventana o presiona Ctrl+C.
echo.
".venv\Scripts\python.exe" -m src.frontend
if errorlevel 1 (
    echo [ERROR] El dashboard termino con un error.
    goto PAUSE_EXIT
)
echo El dashboard se cerro.
pause
goto MENU

:PAUSE_EXIT
echo.
pause
exit /b 1

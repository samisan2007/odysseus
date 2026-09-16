@echo off
setlocal enabledelayedexpansion
title Odysseus - Starting...
set "PROJECT_DIR=C:\Projects\Odysseusai"

echo Checking Docker...
docker info >nul 2>&1
if errorlevel 1 (
    echo Docker Desktop is not running. Starting it now...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    echo Waiting for Docker to be ready, this can take a minute...
    :waitdocker
    timeout /t 5 /nobreak >nul
    docker info >nul 2>&1
    if errorlevel 1 goto waitdocker
    echo Docker is ready.
)

echo Starting Odysseus containers...
cd /d "%PROJECT_DIR%"
docker compose up -d
if errorlevel 1 (
    echo.
    echo Something went wrong starting Odysseus. See the error above.
    pause
    exit /b 1
)

echo Waiting for Odysseus to respond on port 7000...
:waitapp
set "STATUS="
for /f %%S in ('curl -s -o nul -w "%%{http_code}" http://localhost:7000 2^>nul') do set "STATUS=%%S"
if not "!STATUS!"=="200" if not "!STATUS!"=="302" (
    timeout /t 2 /nobreak >nul
    goto waitapp
)

echo Odysseus is up. Opening browser...
start "" "http://localhost:7000"
timeout /t 2 /nobreak >nul
exit

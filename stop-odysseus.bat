@echo off
title Odysseus - Stopping...
cd /d "C:\Projects\Odysseusai"
echo Stopping Odysseus containers...
docker compose stop
echo.
echo Odysseus stopped. Docker Desktop is still running in the background.
pause

@echo off
title GV_DP
cd /d "%~dp0"
echo.
echo   GV_DP - Generateur de Declaration Prealable (ombrieres PV)
echo   Ouverture sur http://localhost:8420
echo.
start "" http://localhost:8420
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8420
pause

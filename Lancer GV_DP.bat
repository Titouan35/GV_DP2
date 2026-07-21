@echo off
title GV_DP
cd /d "%~dp0"

REM Environnement Python : local au poste en priorite (installe par
REM "Installer GV_DP.bat", hors OneDrive), sinon le .venv du dossier (poste de
REM developpement). Ne JAMAIS lancer avec --reload : le watcher scannerait
REM tout le dossier OneDrive synchronise.
set "PYEXE=%LOCALAPPDATA%\GV_DP\.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=.venv\Scripts\python.exe"

if not exist "%PYEXE%" (
  echo.
  echo   GV_DP n'est pas encore installe sur ce poste.
  echo   Lance d'abord "Installer GV_DP.bat" ^(une seule fois^).
  echo.
  pause
  exit /b 1
)

echo.
echo   GV_DP - Generateur de Declaration Prealable ^(ombrieres PV^)
echo   Ouverture sur http://localhost:8420
echo.
echo   Garde cette fenetre ouverte pendant que tu travailles.
echo   Ferme-la pour arreter l'outil.
echo.

start "" http://localhost:8420
"%PYEXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8420
pause

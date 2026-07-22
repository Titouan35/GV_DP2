@echo off
title GV_DP
cd /d "%~dp0"

REM Moteur Python, par ordre de preference :
REM   1. runtime\ : Python PORTABLE embarque dans le dossier OneDrive partage
REM      -> RIEN a installer, un double-clic suffit (poste d'un collegue)
REM   2. l'installation locale posee par "Installer GV_DP.bat"
REM   3. le .venv du repo (poste de developpement)
REM Ne JAMAIS lancer avec --reload : le watcher scannerait tout OneDrive.
set "PYEXE=%~dp0runtime\python.exe"
if not exist "%PYEXE%" set "PYEXE=%LOCALAPPDATA%\GV_DP\.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=%~dp0.venv\Scripts\python.exe"

if not exist "%PYEXE%" (
  echo.
  echo   Aucun moteur Python trouve.
  echo   Si le dossier vient d'etre partage avec toi, attends la fin de la
  echo   synchronisation OneDrive du sous-dossier "runtime" puis relance.
  echo.
  pause
  exit /b 1
)

REM Chaque poste execute le runtime partage : interdire l'ecriture des .pyc,
REM sinon chaque lancement polluerait OneDrive de fichiers compiles locaux.
set PYTHONDONTWRITEBYTECODE=1

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

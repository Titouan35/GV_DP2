@echo off
setlocal enabledelayedexpansion
title Installation de GV_DP
cd /d "%~dp0"

echo.
echo   ============================================
echo     GV_DP - Installation sur ce poste
echo   ============================================
echo.
echo   Le code reste dans OneDrive (mis a jour automatiquement).
echo   Seul l'environnement Python est installe en local, sur ce PC.
echo.

REM --- 1. Python present ? (Store ou systeme ; python.org est bloque au bureau)
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
  echo   [X] Python n'est pas installe sur ce poste.
  echo.
  echo   Installe-le depuis le Microsoft Store ^(gratuit, sans droits admin^) :
  echo     1. Ouvre le Microsoft Store
  echo     2. Cherche "Python 3.13"
  echo     3. Installe, puis relance ce fichier
  echo.
  pause
  exit /b 1
)
echo   [1/4] Python detecte.

REM --- 2. environnement LOCAL, hors OneDrive (sinon des milliers de fichiers
REM         se synchroniseraient inutilement et l'outil serait tres lent)
set "VENV=%LOCALAPPDATA%\GV_DP\.venv"
if exist "%VENV%\Scripts\python.exe" (
  echo   [2/4] Environnement deja present, mise a jour...
) else (
  echo   [2/4] Creation de l'environnement local ^(1 a 2 minutes^)...
  %PY% -m venv "%VENV%"
  if errorlevel 1 (
    echo   [X] Echec de creation de l'environnement.
    pause
    exit /b 1
  )
)

REM --- 3. dependances depuis PyPI (accessible au bureau)
echo   [3/4] Installation des composants ^(2 a 4 minutes^)...
"%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo   [X] Echec de l'installation des composants.
  echo       Verifie ta connexion, puis relance ce fichier.
  pause
  exit /b 1
)

REM --- 4. raccourci sur le Bureau
echo   [4/4] Creation du raccourci sur le Bureau...
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\GV_DP.lnk');" ^
  "$s.TargetPath='%~dp0Lancer GV_DP.bat'; $s.WorkingDirectory='%~dp0';" ^
  "$s.Description='Generateur de Declaration Prealable - ombrieres PV'; $s.Save()" >nul 2>&1

echo.
echo   ============================================
echo     Installation terminee.
echo   ============================================
echo.
echo   Lance l'outil par le raccourci "GV_DP" sur ton Bureau,
echo   ou par "Lancer GV_DP.bat" dans ce dossier.
echo.
pause

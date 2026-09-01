@echo off
setlocal
title GV_DP
cd /d "%~dp0"

REM ===========================================================================
REM  GV_DP - lanceur
REM
REM  Reecrit le 01/09/2026 apres diagnostic de deux pannes reelles :
REM
REM  1. "L'outil s'ouvre puis se fige."
REM     uvicorn ecrit son journal d'acces sur la SORTIE STANDARD. L'ancien
REM     lanceur ne redirigeait que la sortie d'erreur, donc ces lignes
REM     s'affichaient dans cette fenetre. Avec le mode QuickEdit de Windows
REM     (actif par defaut), un simple clic dans la fenetre selectionne du
REM     texte et BLOQUE toute ecriture : le serveur se fige, sans trace.
REM     Correctif : TOUTE la sortie part dans le journal, plus rien ne
REM     s'ecrit dans cette fenetre pendant que le serveur tourne.
REM
REM  2. "La fenetre se ferme aussitot."
REM     Plusieurs sorties d'erreur quittaient sans pause, et le "choice" du
REM     controle OneDrive s'arretait net des que PowerShell renvoyait un code
REM     non nul (chemin avec apostrophe, crochet, strategie d'execution...).
REM     Correctif : plus aucune question bloquante, et toute sortie anormale
REM     laisse la fenetre ouverte avec un message lisible.
REM
REM  Ne JAMAIS lancer avec --reload : le watcher scannerait tout OneDrive.
REM ===========================================================================

set "PORT=8420"
set "URL=http://localhost:%PORT%"

REM --- Journal local, hors OneDrive. On CONSERVE le lancement precedent :
REM     c'est la seule piece a conviction en cas de panne.
set "LOGDIR=%LOCALAPPDATA%\GV_DP"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
set "LOGFILE=%LOGDIR%\gvdp_launch.log"
set "LOGPREV=%LOGDIR%\gvdp_launch.precedent.log"
if exist "%LOGFILE%" move /y "%LOGFILE%" "%LOGPREV%" >nul 2>&1

REM --- 1. Moteur Python, par ordre de preference :
REM       runtime\ (portable, partage) puis installation locale puis .venv du repo
set "PYEXE=%~dp0runtime\python.exe"
if not exist "%PYEXE%" set "PYEXE=%LOCALAPPDATA%\GV_DP\.venv\Scripts\python.exe"
if not exist "%PYEXE%" set "PYEXE=%~dp0.venv\Scripts\python.exe"

if not exist "%PYEXE%" (
  echo.
  echo   [X] Aucun moteur Python trouve.
  echo.
  echo   Si le dossier vient d'etre partage avec toi, attends la fin de la
  echo   synchronisation OneDrive du sous-dossier "runtime", puis relance.
  echo   Sinon, lance d'abord "Installer GV_DP.bat".
  echo.
  pause
  exit /b 1
)

REM --- 2. Deja demarre ? On ouvre le navigateur au lieu d'echouer sur le port.
REM     On interroge /api/sante et on verifie que c'est bien GV_DP qui repond :
REM     n'importe quel autre outil ecoutant sur 8420 faisait ouvrir le navigateur
REM     dessus et abandonner le lancement (releve le 01/09/2026).
powershell -NoProfile -ExecutionPolicy Bypass -Command "try{$r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri ($env:URL + '/api/sante'); if(($r.Content | ConvertFrom-Json).app -eq 'GV_DP'){exit 0}else{exit 1}}catch{exit 1}" >nul 2>&1
if not errorlevel 1 (
  echo.
  echo   GV_DP tourne deja. Ouverture du navigateur sur %URL%
  echo.
  start "" "%URL%"
  timeout /t 3 >nul
  exit /b 0
)

REM --- 2 bis. Port pris par un AUTRE programme : le dire, plutot que de laisser
REM     uvicorn echouer sur un message de socket incomprehensible.
netstat -ano | findstr /R /C:"LISTENING" | findstr /C:":%PORT% " >nul 2>&1
if not errorlevel 1 (
  echo.
  echo   [X] Le port %PORT% est deja utilise par un autre programme
  echo       ^(ce n'est pas GV_DP : la verification vient d'echouer^).
  echo.
  echo   Ferme ce programme, ou previens Florent pour changer de port.
  echo.
  pause
  exit /b 1
)

REM --- 3. Interdire l'ecriture des .pyc : le runtime est partage par OneDrive,
REM        chaque lancement polluerait le dossier de fichiers compiles locaux.
set PYTHONDONTWRITEBYTECODE=1

echo.
echo   GV_DP - Generateur de Declaration Prealable ^(ombrieres PV^)
echo.
echo   Demarrage du serveur, le navigateur s'ouvrira tout seul.
echo   Premiere fois sur ce poste : jusqu'a 1 a 2 minutes.
echo.
echo   Garde cette fenetre ouverte pendant que tu travailles.
echo   Ferme-la pour arreter l'outil.
echo.
echo   Adresse : %URL%
echo   Journaux : %LOGFILE%
echo              %LOGDIR%\gvdp_navigateur.log
echo.

REM --- 4. Guetteur d'ouverture du navigateur, dans un fichier .ps1 dedie.
REM        Voir scripts\ouvrir_navigateur.ps1 pour le detail des pieges evites.
if exist "%~dp0scripts\ouvrir_navigateur.ps1" (
  start "" /b powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ouvrir_navigateur.ps1" -Url "%URL%" -Log "%LOGFILE%"
) else (
  echo   [!] scripts\ouvrir_navigateur.ps1 est introuvable.
  echo       Ouvre %URL% a la main quand le serveur sera pret.
  echo.
)

REM --- 5. Le serveur. TOUTE sa sortie va dans le journal : c'est ce qui
REM        empeche le gel par QuickEdit decrit en tete de fichier.
echo [%date% %time%] ----- lancement avec %PYEXE% >> "%LOGFILE%"
"%PYEXE%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% >> "%LOGFILE%" 2>&1
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" (
  echo   Serveur arrete normalement.
) else (
  echo   [X] Le serveur s'est arrete de facon inattendue ^(code %CODE%^).
  echo.
  echo   Dernieres lignes du journal :
  echo   ---------------------------------------------------------------
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try{Get-Content -LiteralPath $env:LOGFILE -Tail 15 | ForEach-Object { '   ' + $_ }}catch{}"
  echo   ---------------------------------------------------------------
  echo.
  echo   Envoie ce fichier a Florent : %LOGFILE%
)
echo.
pause
exit /b %CODE%

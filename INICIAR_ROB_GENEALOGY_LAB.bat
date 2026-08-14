@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ROB] Preparando entorno local por primera vez...
  py -3 -m venv .venv
  if errorlevel 1 goto :error
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  if errorlevel 1 goto :error
  ".venv\Scripts\python.exe" -m pip install -e .
  if errorlevel 1 goto :error
)

if not exist "%USERPROFILE%\Downloads\ROB-Genealogy-Lab" mkdir "%USERPROFILE%\Downloads\ROB-Genealogy-Lab"

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Milliseconds 1200; Start-Process 'http://127.0.0.1:8877'"

echo [ROB] Iniciando ROB Genealogy Lab en http://127.0.0.1:8877
".venv\Scripts\python.exe" -m rob.lab.local_app
goto :eof

:error
echo.
echo [ROB] No se pudo preparar la aplicacion.
pause
exit /b 1

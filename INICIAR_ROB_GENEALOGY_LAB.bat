@echo off
setlocal
cd /d "%~dp0"

rem ROB always uses the folder containing this launcher as its canonical local root.
set "ROB_LAB_HOME=%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ROB] Preparando entorno local por primera vez...
  py -3 -m venv .venv
  if errorlevel 1 goto :error
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  if errorlevel 1 goto :error
  ".venv\Scripts\python.exe" -m pip install -e .
  if errorlevel 1 goto :error
)

set "ROB_CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "ROB_CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined ROB_CHROME if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "ROB_CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined ROB_CHROME if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "ROB_CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

if defined ROB_CHROME (
  start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Milliseconds 1300; Start-Process '%ROB_CHROME%' 'http://127.0.0.1:8877'"
) else (
  start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Milliseconds 1300; Start-Process 'http://127.0.0.1:8877'"
)

echo [ROB] ROB Genealogy Lab activo.
echo [ROB] Raiz: %ROB_LAB_HOME%
echo [ROB] Puedes minimizar esta ventana. Para cerrar ROB: Ctrl+C.
echo.

".venv\Scripts\python.exe" -c "import uvicorn; from rob.lab.local_app import app; uvicorn.run(app, host='127.0.0.1', port=8877, log_level='warning', access_log=False)"
goto :eof

:error
echo.
echo [ROB] No se pudo preparar la aplicacion.
pause
exit /b 1

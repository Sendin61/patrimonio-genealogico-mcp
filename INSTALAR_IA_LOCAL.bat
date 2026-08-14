@echo off
setlocal
cd /d "%~dp0"
echo [ROB] Preparando la IA local dentro de esta carpeta...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALAR_IA_LOCAL.ps1"
if errorlevel 1 (
  echo.
  echo [ROB] La instalacion no termino correctamente.
  pause
  exit /b 1
)
echo.
echo [ROB] Instalacion terminada.
pause

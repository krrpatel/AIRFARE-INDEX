@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
if not exist "frontend\node_modules" (
  echo Frontend dependencies are missing. Run setup.bat first.
  pause
  exit /b 1
)

set PYTHONPATH=%~dp0
start "Airfare Backend" /D "%~dp0" cmd /k "call .venv\Scripts\activate.bat && set PYTHONPATH=%~dp0 && python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000"
start "Airfare Frontend" /D "%~dp0frontend" cmd /k "npm run dev"

timeout /t 3 /nobreak >nul
start "" http://127.0.0.1:3000
echo Backend: http://127.0.0.1:8000
echo Frontend: http://127.0.0.1:3000

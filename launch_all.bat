@echo off
setlocal

cd /d "%~dp0"

echo ==========================================
echo  My Duka POS - Launching all services
echo ==========================================
echo Tip: For silent start (no CMD windows), use launch_all.vbs

REM Ensure clean start (prevents duplicate processes / stale UI)
call "%~dp0stop_all.bat"
powershell -NoProfile -Command "Start-Sleep -Seconds 1"

REM Ensure .env exists (services require it now)
if not exist "%~dp0.env" (
  echo [ERROR] Missing .env in project folder: %~dp0
  echo Copy .env.example to .env and fill your keys/passwords.
  echo.
  pause
  exit /b 1
)

REM Use venv python if available
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] venv python not found at "%PY%"
  echo Please create venv or update this script.
  pause
  exit /b 1
)

REM ngrok executable
set "NGROK=%~dp0ngrok.exe"
if not exist "%NGROK%" (
  echo [ERROR] ngrok.exe not found at "%NGROK%"
  echo Put ngrok.exe in the project folder: %~dp0
  pause
  exit /b 1
)

REM Start services in separate windows (avoid cmd /k wrapper)
start "POS Dashboard (8080)" "%PY%" "%~dp0dashboard.py"
powershell -NoProfile -Command "Start-Sleep -Seconds 2"

start "M-Pesa Callback (5000)" "%PY%" "%~dp0mpesa_callback.py"
powershell -NoProfile -Command "Start-Sleep -Seconds 2"

start "ngrok -> 5000" "%NGROK%" http 5000
powershell -NoProfile -Command "Start-Sleep -Seconds 2"

start "Telegram Bot" "%PY%" "%~dp0telegram_bot.py"

REM Open dashboard in browser
start "" "http://127.0.0.1:8080"

echo.
echo All services started.
echo - Dashboard: http://127.0.0.1:8080
echo - Callback local: http://127.0.0.1:5000/mpesa/test
echo - ngrok UI: http://127.0.0.1:4040
echo.
echo To stop everything, run stop_all.bat
echo ==========================================

endlocal

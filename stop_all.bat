@echo off
setlocal

echo ==========================================
echo  My Duka POS - Stopping services
echo ==========================================

REM Kill processes by window title (best effort)
taskkill /FI "WINDOWTITLE eq POS Dashboard (8080)*" /T /F
taskkill /FI "WINDOWTITLE eq M-Pesa Callback (5000)*" /T /F
taskkill /FI "WINDOWTITLE eq ngrok -> 5000*" /T /F
taskkill /FI "WINDOWTITLE eq Telegram Bot*" /T /F

REM Kill by command line (kills ALL duplicates reliably)
powershell -NoProfile -Command ^
  "$rx='dashboard\.py|mpesa_callback\.py|telegram_bot\.py';" ^
  "$pids = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $rx } | ForEach-Object { $_.ProcessId });" ^
  "if($pids.Count -gt 0){ Stop-Process -Id $pids -Force -ErrorAction SilentlyContinue }"

REM Kill ngrok (all instances)
taskkill /IM ngrok.exe /T /F

REM Also kill by listening ports (in case titles differ)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8080 ^| findstr LISTENING') do taskkill /PID %%p /T /F
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING') do taskkill /PID %%p /T /F
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :4040 ^| findstr LISTENING') do taskkill /PID %%p /T /F

echo Done.
echo ==========================================
endlocal

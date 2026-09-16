@echo off
chcp 65001 >nul
echo Requesting shutdown of server...
curl -s -m 5 http://127.0.0.1:8765/api/shutdown >nul 2>nul
if errorlevel 1 (
  echo [INFO] Could not reach the server - it may already be stopped.
) else (
  echo Shutdown request sent. MATLAB worker will exit too.
)
timeout /t 2 >nul
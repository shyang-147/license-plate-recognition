@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "HERE=%~dp0"
if not exist "%HERE%lpr_server.py" (
  echo ============================================================
  echo  [ERROR] lpr_server.py is NOT in this folder:
  echo      %HERE%
  echo  lpr_server.py and this .bat must stay in the SAME folder.
  echo ============================================================
  pause
  exit /b 1
)
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.8+ from python.org
  pause
  exit /b 1
)
rem ---- a random access code is generated every run ----
set "LPR_TOKEN="
for /f "delims=" %%i in ('python -c "import secrets;print(secrets.token_urlsafe(6))"') do set "LPR_TOKEN=%%i"
if "%LPR_TOKEN%"=="" set "LPR_TOKEN=lpr2026"
set LPR_HOST=0.0.0.0
set LPR_PUBLIC=1
if "%LPR_PORT%"=="" set LPR_PORT=8765
echo ============================================================
echo  License plate recognition - PUBLIC MODE (cloudflared tunnel)
echo.
echo  Access code for this run :  %LPR_TOKEN%
echo  ^(the page asks for it; share the code only with people you trust^)
echo.
echo  A public https://xxxx.trycloudflare.com address will be printed
echo  below - open it on ANY device (4G / other WiFi, no port forward).
echo  It stops working as soon as you close this window.
echo  Press Ctrl+C to stop.
echo ============================================================
python "%HERE%lpr_server.py"
echo.
echo Server stopped.
pause
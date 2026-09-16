@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "HERE=%~dp0"
if not exist "%HERE%lpr_server.py" (
  echo ============================================================
  echo  [ERROR] lpr_server.py is NOT in this folder:
  echo      %HERE%
  echo.
  echo  lpr_server.py and this .bat must stay in the SAME folder.
  echo  Please put them together and run this .bat again.
  echo ============================================================
  pause
  exit /b 1
)
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.8+ from python.org
  echo         and tick "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)
set LPR_HOST=0.0.0.0
if "%LPR_PORT%"=="" set LPR_PORT=8765
echo ============================================================
echo  License plate recognition - LAN MODE
echo.
echo  On THIS PC   : http://127.0.0.1:%LPR_PORT%
echo  On phone/PC  : the "LAN" address printed below (same WiFi)
echo.
echo  If Windows Firewall asks, tick "Private networks" + Allow.
echo  Press Ctrl+C in this window to stop.
echo ============================================================
python "%HERE%lpr_server.py"
echo.
echo Server stopped.
pause
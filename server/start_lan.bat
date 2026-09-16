@echo off
chcp 65001 >nul
cd /d "%~dp0"
set LPR_HOST=0.0.0.0
set LPR_PORT=8765
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found. Please install Python 3.8+ and add it to PATH.
  pause
  exit /b 1
)
echo ============================================================
echo  Starting license plate recognition web app - LAN MODE
echo.
echo  On THIS computer   : http://127.0.0.1:8765
echo  On phone / others  : use the "LAN" address printed below
echo.
echo  If Windows Firewall pops up, tick "Private networks" and
echo  click Allow, otherwise other devices cannot connect.
echo.
echo  WARNING: this server has NO password. Only use it on a
echo  trusted home / office LAN. Press Ctrl+C here to stop.
echo ============================================================
python server.py
echo.
echo Server stopped.
pause

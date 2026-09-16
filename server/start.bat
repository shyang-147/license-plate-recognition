@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found. Please install Python 3.8+ and add it to PATH.
  pause
  exit /b 1
)
echo ============================================================
echo  Starting license plate recognition web app...
echo  Browser will open http://127.0.0.1:8765 automatically
echo  First recognition needs ~10s to warm up the MATLAB engine
echo  Press Ctrl+C in this window to stop
echo ============================================================
python server.py
echo.
echo Server stopped.
pause
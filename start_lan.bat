@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
set PORT=8080

echo ============================================================
echo  车牌识别网页 - 局域网共享
echo ============================================================
echo.
echo 正在查找本机 IP ...
set IP=
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    if "!IP!"=="" set IP=%%a
)
set IP=%IP: =%
if "%IP%"=="" set IP=127.0.0.1

echo.
echo   本机打开:   http://127.0.0.1:%PORT%/
echo   手机/平板:  http://%IP%:%PORT%/
echo.
echo   (手机需要和这台电脑连同一个 WiFi)
echo   关掉这个黑窗口 = 停止共享, 别人就打不开了
echo ============================================================
echo.

where python >nul 2>nul
if %errorlevel%==0 (
    python -m http.server %PORT%
) else (
    where py >nul 2>nul
    if !errorlevel!==0 (
        py -m http.server %PORT%
    ) else (
        echo [错误] 没找到 python。可以改用"网上托管"的办法:
        echo        把 index.html 拖到 https://app.netlify.com/drop 即可得到网址。
    )
)
pause

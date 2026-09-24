@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo   Pictomapart - 打包成单文件 exe
echo   ==============================
echo.
python tools\build.py %*
if errorlevel 1 (
    echo.
    echo   打包失败。
)
echo.
pause

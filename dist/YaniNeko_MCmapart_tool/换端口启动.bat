@echo off
title YaniNeko_MCmapart_tool - 端口 8899
set MAPART_PORT=8899
echo.
echo   正在用 8899 端口启动 YaniNeko_MCmapart_tool ...
echo   浏览器会自动打开 http://127.0.0.1:8899
echo   关闭本窗口即停止服务。
echo.
"%~dp0YaniNeko_MCmapart_tool.exe"

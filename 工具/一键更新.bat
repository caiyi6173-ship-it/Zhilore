@echo off
chcp 65001 >nul
title Zhihu Knowledge Update
cd /d "%~dp0"

echo [1/3] Capturing Zhihu favorites...
"D:\python\python.exe" "抓取.py"
if errorlevel 1 goto fail

echo.
echo [2/3] Rebuilding article folders with LLM/fallback splitter...
"D:\python\python.exe" "知识重构.py"
if errorlevel 1 goto fail

echo.
echo [3/3] Building section links and indexes...
"D:\python\python.exe" "处理.py"
if errorlevel 1 goto fail

echo.
echo Update complete. Run 启动网页.bat to browse.
pause
exit /b 0

:fail
echo.
echo Update failed. Read the message above.
pause
exit /b 1

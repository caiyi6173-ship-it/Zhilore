@echo off
chcp 936 >nul
title 知乎收藏夹 · 知识库
cd /d "%~dp0"
echo 正在启动知识库网页... 浏览器会自动打开。
echo 关闭这个窗口即可停止服务。
echo.
"D:/python/python.exe" "服务.py"
pause

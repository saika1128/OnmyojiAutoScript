@echo off
chcp 936>nul
title RYH Backend - port 22288
cd /d "%~dp0"
echo ==================================================
echo   RYH backend (New / mine)  -^>  http://127.0.0.1:22288
echo   Keep this window OPEN while running.
echo ==================================================
"E:\»æ¾í\OnmyojiAutoScript\.venv\Scripts\python.exe" server.py
echo.
echo Backend stopped. Press any key to close.
pause>nul

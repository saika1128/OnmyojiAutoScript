@echo off
chcp 936>nul
title VoidElite Runner - 999 loop
cd /d "%~dp0"
set "PYTHONPATH=%~dp0"
echo ==================================================
echo   VoidElite runner (dev-act)
echo   999 battles -^> pause current-minute -^> repeat
echo   Keep this window OPEN while running.
echo   Close this window to stop the loop.
echo ==================================================
"%~dp0..\OnmyojiAutoScript\.venv\Scripts\python.exe" -u -m tasks.VoidElite.loop_runner
echo.
echo Runner stopped. Press any key to close.
pause>nul

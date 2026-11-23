@echo off
rem Double-click: grades finished picks, prices upcoming NFL + soccer games, logs new picks.
cd /d "%~dp0"
call "%~dp0betmodel.cmd" daily %*
echo.
pause

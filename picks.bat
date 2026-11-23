@echo off
rem Double-click: grade finished picks, show the record / ROI / closing-line value, export to CSV.
cd /d "%~dp0"
call "%~dp0betmodel.cmd" picks settle
call "%~dp0betmodel.cmd" picks report --last 40
call "%~dp0betmodel.cmd" picks export --out picks_export.csv
echo.
pause

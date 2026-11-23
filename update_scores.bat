@echo off
rem Double-click: FREE update from ESPN - live/final scores, grade picks, compare results with the
rem model's predictions, refresh the phone page. Uses no Odds API credits and does not re-run the model.
cd /d "%~dp0"
call "%~dp0betmodel.cmd" scores
echo.
pause

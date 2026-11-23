@echo off
rem Double-click: paste your keys (right-click pastes in this window). Press Enter on its own to skip one.
cd /d "%~dp0"
echo.
echo Paste each value and press Enter. Press Enter on its own to keep what's already saved.
echo.
set "ODDS="
set "AF="
set "GH="
set "REPO="
set /p "ODDS=The Odds API key                     : "
set /p "AF=API-Football key                     : "
set /p "GH=GitHub token (view runs from anywhere): "
set /p "REPO=GitHub repo, as yourname/reponame    : "
echo.
if defined ODDS call "%~dp0betmodel.cmd" keys save odds %ODDS%
if defined AF call "%~dp0betmodel.cmd" keys save apifootball %AF%
if defined GH call "%~dp0betmodel.cmd" keys save github %GH%
if defined REPO call "%~dp0betmodel.cmd" keys save repo %REPO%
echo.
echo Testing (these checks do not use up any of your free quota)...
echo.
call "%~dp0betmodel.cmd" keys check
echo.
pause

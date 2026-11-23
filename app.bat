@echo off
title OddSea
rem Double-click: opens OddSea in your browser. Nothing runs until you press "Run model".
rem Close this window when you're done to shut the app down.
cd /d "%~dp0"
call "%~dp0betmodel.cmd" app
pause

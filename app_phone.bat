@echo off
title OddSea (phone)
rem Double-click: runs OddSea so your phone can open it too (home Wi-Fi, or anywhere with Tailscale).
rem It prints the address to type on your phone. Close this window to stop it.
cd /d "%~dp0"
call "%~dp0betmodel.cmd" app --phone
pause

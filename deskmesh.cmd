@echo off
rem DeskMesh launcher. Double-click this file, or run "deskmesh" in a terminal.
rem A .cmd file is not subject to PowerShell's execution policy, so there are no
rem flags here to remember. Arguments pass straight through to start.ps1, which
rem also decides whether to hold the window open after a failure.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
exit /b %ERRORLEVEL%

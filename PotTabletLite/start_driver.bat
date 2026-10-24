@echo off
rem Runs the PotTabletLite driver in this console window. Press Ctrl+C to stop.
cd /d "%~dp0"
python -m potlite.driver %*
pause

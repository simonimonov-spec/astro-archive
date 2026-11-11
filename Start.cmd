@echo off
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\pythonw.exe" goto setup
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0start.pyw"
exit /b
:setup
echo Run Setup.cmd first to install the Python libraries for this copy.
pause

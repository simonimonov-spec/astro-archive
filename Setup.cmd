@echo off
cd /d "%~dp0"
py -3.13 -m venv "%~dp0.venv"
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" -m pip install --disable-pip-version-check --only-binary=:all: -r "%~dp0requirements-lock.txt"
if errorlevel 1 goto failed
echo Ready. Open Start.cmd.
echo Optional plugins are installed separately. See docs\PLUGINS.md.
pause
exit /b
:failed
echo Setup failed. Install Python 3.13 with Python Launcher, then try again.
pause

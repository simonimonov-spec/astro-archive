@echo off
setlocal
"%~dp0..\..\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo Login support could not be installed.
  pause
  exit /b 1
)
echo Login support is ready. Open Astro Archive and enable IASC Campaigns.
pause

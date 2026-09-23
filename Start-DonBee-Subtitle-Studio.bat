@echo off
rem DonBee Subtitle Studio launcher
setlocal
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"
if defined STUDIO_MODEL_PYTHON goto :custom
set "STUDIO_BOOTSTRAP_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "STUDIO_BOOTSTRAP_PYTHON=%~dp0.venv\Scripts\python.exe"
"%STUDIO_BOOTSTRAP_PYTHON%" --version >nul 2>&1
if not errorlevel 1 goto :bootstrap
echo [DonBee] Python 3.12 is missing. Installation size is unknown.
echo [DonBee] Install Python.Python.3.12 using winget? This changes your system.
choice /c YN /n /t 30 /d N /m "Install Python? [Y/N]: "
if errorlevel 2 exit /b 2
winget install --id Python.Python.3.12 --exact --source winget --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto :python_failed
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "STUDIO_BOOTSTRAP_PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
"%STUDIO_BOOTSTRAP_PYTHON%" --version >nul 2>&1
if errorlevel 1 goto :python_failed
:bootstrap
"%STUDIO_BOOTSTRAP_PYTHON%" -m bootstrap --interactive --serve %*
set "STUDIO_LAUNCH_EXIT=%errorlevel%"
if not "%STUDIO_LAUNCH_EXIT%"=="0" pause
exit /b %STUDIO_LAUNCH_EXIT%
:custom
rem 自訂環境不自動安裝或改版；由 agent 按 SETUP 檢查後使用。
echo [DonBee] Custom Python: automatic installation is disabled. See guides/SETUP.md.
set "PYTHONPATH=%~dp0src"
if exist "%~dp0.venv-download\Scripts\python.exe" set "STUDIO_YTDLP_PYTHON=%~dp0.venv-download\Scripts\python.exe"
"%STUDIO_MODEL_PYTHON%" -m app serve --host 127.0.0.1 --port 8765 --open-browser
exit /b %errorlevel%
:python_failed
echo [DonBee] Python installation could not be verified. Reopen this launcher after installation.
pause
exit /b 1

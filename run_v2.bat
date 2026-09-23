@echo off
setlocal
cd /d "%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
rem 進階：自訂 STUDIO_MODEL_PYTHON 時直接用該直譯器啟動（跳過啟動自檢）。
if defined STUDIO_MODEL_PYTHON (
  set "PYTHONPATH=%~dp0src"
  if exist "%~dp0.venv-download\Scripts\python.exe" set "STUDIO_YTDLP_PYTHON=%~dp0.venv-download\Scripts\python.exe"
  "%STUDIO_MODEL_PYTHON%" -m app serve --host 127.0.0.1 --port 8765 --open-browser
  goto :end
)
rem 預設：啟動自檢（第二次起走快速路徑）→ 缺什麼補什麼（.venv、套件、模型提示）→ 以專案 .venv 啟動服務。
where python >nul 2>&1
if errorlevel 1 (
  echo [冬比字幕工作室] 找不到 Python 3.12，請先安裝：winget install Python.Python.3.12
  pause
  goto :end
)
python -m bootstrap --serve
if errorlevel 1 pause
:end
endlocal

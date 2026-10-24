@echo off
setlocal
cd /d "%~dp0"
set "DETRETLABEL_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "DETRETLABEL_PYTHON=.venv\Scripts\python.exe"
"%DETRETLABEL_PYTHON%" -c "import PySide6, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo Python or required dependencies were not found.
    echo Run: python -m venv .venv
    echo Run: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
"%DETRETLABEL_PYTHON%" main.py
if errorlevel 1 pause

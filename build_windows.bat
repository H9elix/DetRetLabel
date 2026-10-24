@echo off
setlocal
cd /d "%~dp0"
set "DETRETLABEL_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "DETRETLABEL_PYTHON=.venv\Scripts\python.exe"
"%DETRETLABEL_PYTHON%" -m pip install -r requirements-build.txt
if errorlevel 1 goto failed
"%DETRETLABEL_PYTHON%" tools\build_windows.py
if errorlevel 1 goto failed
echo Packages are ready in dist\windows-x64
pause
exit /b 0
:failed
echo Build failed. Check build\windows-x64\pyinstaller.log and artifacts\packaging.
pause
exit /b 1

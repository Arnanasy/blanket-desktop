@echo off
cd /d "%~dp0"
if not exist "C:\Users\66998\miniconda3\pythonw.exe" goto missingpython
if not exist "%~dp0launch.pyw" goto missingfiles
if not exist "%~dp0hide_desktop_icons.py" goto missingfiles
if not exist "%~dp0desktop_widgets.py" goto missingfiles
if not exist "%~dp0mouse_events.py" goto missingfiles
start "" "C:\Users\66998\miniconda3\pythonw.exe" "%~dp0launch.pyw"
exit /b
:missingpython
echo Python is missing: C:\Users\66998\miniconda3\pythonw.exe
pause
exit /b 1
:missingfiles
echo Program files are missing. Keep this launcher and all Python files together.
pause
exit /b 1

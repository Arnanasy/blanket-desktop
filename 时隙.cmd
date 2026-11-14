@echo off
setlocal
cd /d "%~dp0"
if not exist "launch.pyw" goto missing
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -B "launch.pyw"
  exit /b
)
for %%P in (pythonw.exe pyw.exe) do if not "%%~$PATH:P"=="" (
  start "" "%%~$PATH:P" -B "launch.pyw"
  exit /b
)
echo Install Python 3 with Tk and add Python to PATH.
pause
exit /b 1
:missing
echo Keep this launcher in the folder containing launch.pyw.
pause
exit /b 1

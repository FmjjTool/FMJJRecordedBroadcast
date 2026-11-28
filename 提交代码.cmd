@echo off
setlocal
chcp 65001 >nul
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 goto use_py
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 goto use_python
echo Python 3.10+ was not found. Please install Python and add it to PATH.
pause
exit /b 2

:use_py
py -3 "%~dp0commit_code.py" --pause %*
exit /b %errorlevel%

:use_python
python "%~dp0commit_code.py" --pause %*
exit /b %errorlevel%

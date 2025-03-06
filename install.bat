@echo off
SETLOCAL ENABLEEXTENSIONS
SETLOCAL ENABLEDELAYEDEXPANSION

echo ==================================
echo    Checking Python installation   
echo ==================================
echo.
python --version 2>NUL
echo.
if errorlevel 1 (
    echo Python is not installed or not in PATH
    echo Please install Python 3.8 or later from https://www.python.org/
    pause
    exit /b 1
)

echo =========================================
echo    Checking Python virtual environment   
echo =========================================

if not exist venv (
echo.
	echo Installing new python environment...
    python -m venv .\venv
)

echo.
if exist venv (
	echo ====================================
	echo    Activating virtual environment   
	echo ====================================
	call venv\Scripts\activate.bat
)

echo.
echo ====================================
echo    Installing Python dependencies   
echo ====================================
echo.
python -m pip install --upgrade pip
pip install html2text requests bs4

echo.
echo =============================
echo    Verifying installations   
echo =============================

python -c "import html2text" 2>NUL
if errorlevel 1 (
    echo Error: html2text installation failed
    pause
    exit /b 1
)
if errorlevel 0 (
    echo Installation verified successfully
)

echo.
echo ====================================
echo    Verifying necessary directories   
echo ====================================

if not exist "in" (
	echo Creating input folder
	mkdir in
)
if not exist "out" (
	echo Creating output folder
	mkdir out
)

echo.
echo ============================
echo    Installation completed   
echo ============================
echo.
echo Directory structure created:
echo - in/    (place your HTML files here)
echo - out/   (converted files will appear here)
echo.
echo Next steps:
echo 1. Place your HTML files in the 'in' folder
echo 2. Run EITHER run.bat OR convert.ps1 to process the files

pause
exit /b 0
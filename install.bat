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
	echo Installing new environment
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
pip install html2text requests bs4 lxml

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

echo.
echo ====================================
echo    Creating necessary directories   
echo ====================================

if not exist "input" mkdir input
if not exist "input-xml" mkdir input-xml
if not exist "output" mkdir output
if not exist "logs" mkdir logs

echo.
echo ============================
echo    Installation completed   
echo ============================
echo.
echo Directory structure created:
echo - input/      (place your HTML files here)
echo - input-xml/  (optionally: place your XML files here)
echo - output/     (converted files will appear here)
echo.
echo Next steps:
echo 1. Place your HTML files in the 'input' folder
echo 2. Place your XML files in the 'input-xml' folder (optionally, but delivers better results)
echo 3. Run EITHER run.bat OR convert.ps1 to process the files

pause
exit /b 0
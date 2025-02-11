@echo off
SETLOCAL ENABLEEXTENSIONS
SETLOCAL ENABLEDELAYEDEXPANSION

echo =================================
echo    Starting conversion process   
echo =================================
echo.

:: Check if PowerShell is available
where powershell >nul 2>&1
if errorlevel 1 (
    echo Error: PowerShell is not installed or not in PATH
    echo Please install PowerShell or add it to your PATH
    pause
    exit /b 1
)

:: Execute PowerShell script with bypass execution policy
powershell -ExecutionPolicy Bypass -File convert.ps1

exit /b %ERRORLEVEL%
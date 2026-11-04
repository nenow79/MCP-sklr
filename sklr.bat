@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Uruchamianie sklr MCP
pushd "%~dp0"
if errorlevel 1 exit /b 1

if not exist "venv\Scripts\python.exe" (
    echo [BLAD] Nie znaleziono lokalnego venv. Najpierw uruchom install.bat.
    popd
    pause
    exit /b 1
)

echo Uruchamianie sklr MCP - domyslny port 8101, panel 8102.
echo Faktyczne porty okresla config.json; otworz aktualny link panelu z konsoli.
"venv\Scripts\python.exe" "main.py"
set "SERVER_RESULT=%ERRORLEVEL%"

echo.
echo Program zakonczyl dzialanie. Kod: %SERVER_RESULT%
popd
pause
exit /b %SERVER_RESULT%

@echo off
title Uruchamianie MCP do vibecoding...
setlocal

:: Przejdź do katalogu, w którym znajduje się plik .bat
cd /d "%~dp0"

:: Sprawdź czy folder venv istnieje
if not exist "venv\Scripts\activate.bat" (
    echo [BLAD] Nie znaleziono srodowiska venv w tym katalogu!
    pause
    exit /b
)

echo Aktywowanie srodowiska venv...
call venv\Scripts\activate.bat

echo Uruchamianie Vibecoder MCP (port 8100)...
python main.py

:: Zapobiega zamknieciu okna po zakonczeniu skryptu (przydatne przy bledach)
echo.
echo Program zakonczyl dzialanie.
pause
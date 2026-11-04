@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Instalacja sklr
pushd "%~dp0"
if errorlevel 1 exit /b 1

set "CONDA_ENV=root"
if not "%~1"=="" set "CONDA_ENV=%~1"

echo Instalator sklr - zatrzymaj dzialajacy serwer przed instalacja.
echo Istniejacy config.json i bazy nie beda nadpisywane.
echo.
if not exist "main.py" goto missing_files
if not exist "requirements.txt" goto missing_files
if not exist "config.example.json" goto missing_files
if not exist "scripts\init_install_config.py" goto missing_files

if exist "venv\Scripts\python.exe" goto dependencies
if exist "venv" goto incomplete_venv

echo Tworzenie venv przez srodowisko conda "%CONDA_ENV%"...
call :create_venv
if errorlevel 1 goto failed

:dependencies
echo Sprawdzanie lokalnego interpretera...
"venv\Scripts\python.exe" -c "import sys; from pathlib import Path; assert sys.prefix != sys.base_prefix, 'Interpreter nie jest venv'; assert Path(sys.prefix).resolve() == Path('venv').resolve(), 'Venv wskazuje inny katalog'; print(sys.version)"
if errorlevel 1 goto failed

echo Instalowanie zaleznosci w lokalnym venv...
"venv\Scripts\python.exe" -m pip install -r "requirements.txt"
if errorlevel 1 goto failed
"venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed

echo Przygotowanie konfiguracji...
"venv\Scripts\python.exe" "scripts\init_install_config.py"
if errorlevel 1 goto failed

echo.
echo [OK] Instalacja zakonczona. Uruchom sklr.bat.
echo W nowym panelu dodaj swoj istniejacy folder projektu, potem nacisnij Start.
echo Instalator nie uruchamia serwera ani nie instaluje skilli lub conda.
popd
pause
exit /b 0

:create_venv
where activate.bat >nul 2>&1
if errorlevel 1 goto use_conda
call activate "%CONDA_ENV%"
if errorlevel 1 exit /b 1
goto conda_ready
:use_conda
where conda >nul 2>&1
if errorlevel 1 goto no_conda
call conda activate "%CONDA_ENV%"
if errorlevel 1 goto no_conda
:conda_ready
python -m venv "venv"
set "CREATE_RESULT=%ERRORLEVEL%"
where conda >nul 2>&1
if errorlevel 1 goto legacy_deactivate
call conda deactivate
if errorlevel 1 exit /b 1
exit /b %CREATE_RESULT%
:legacy_deactivate
call deactivate
if errorlevel 1 exit /b 1
exit /b %CREATE_RESULT%
:no_conda
echo [BLAD] Nie mozna aktywowac conda. Otworz Anaconda/Miniforge Prompt.
echo Przejdz do folderu sklr i wykonaj: install.bat root
echo Jesli srodowisko nazywa sie base, wykonaj: install.bat base
exit /b 1

:missing_files
echo [BLAD] Niepelna paczka projektu. Pobierz caly projekt, nie sam install.bat.
goto failed
:incomplete_venv
echo [BLAD] Folder venv istnieje, ale brak venv\Scripts\python.exe.
echo Nie nadpisujemy go. Sprawdz go i przenies na bok przed ponowna instalacja.
:failed
echo.
echo [BLAD] Instalacja nie zostala zakonczona. Sprawdz komunikat powyzej.
echo Zaleznosci lub venv moga byc czesciowo zainstalowane; brak automatycznego rollbacku.
popd
pause
exit /b 1

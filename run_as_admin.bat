@echo off
setlocal EnableExtensions

set "APP_DIR=%~dp0"
set "OVERLAY_PATH=%APP_DIR%overlay.py"
set "SETUP_PATH=%APP_DIR%setup.py"
set "PY_EXE="
set "PYW_EXE="
set "PATH_PYTHON_SEEN="

call :try_python_pair "%APP_DIR%.venv\Scripts"
if not defined PY_EXE call :try_python_pair "%APP_DIR%venv\Scripts"
if not defined PY_EXE call :try_path_python

if not defined PY_EXE (
    set "PRECHECK_LOG=%TEMP%\HeatMap_launcher_%RANDOM%%RANDOM%.txt"
    > "%PRECHECK_LOG%" echo HeatMap launcher could not find a usable Python interpreter.
    >> "%PRECHECK_LOG%" echo.
    >> "%PRECHECK_LOG%" echo Checked:
    >> "%PRECHECK_LOG%" echo   %APP_DIR%.venv\Scripts\python.exe
    >> "%PRECHECK_LOG%" echo   %APP_DIR%venv\Scripts\python.exe
    >> "%PRECHECK_LOG%" echo   python.exe from PATH with adjacent pythonw.exe
    >> "%PRECHECK_LOG%" echo.
    >> "%PRECHECK_LOG%" echo Create .venv and install dependencies:
    >> "%PRECHECK_LOG%" echo   python -m pip install -r requirements.txt
    call :show_error_from_log
    del "%PRECHECK_LOG%" >nul 2>nul
    exit /b 1
)

set "PRECHECK_LOG=%TEMP%\HeatMap_preflight_%RANDOM%%RANDOM%.txt"
"%PY_EXE%" "%SETUP_PATH%" --preflight > "%PRECHECK_LOG%" 2>&1
if errorlevel 1 (
    > "%PRECHECK_LOG%.message" echo HeatMap preflight failed.
    >> "%PRECHECK_LOG%.message" echo.
    >> "%PRECHECK_LOG%.message" echo Selected Python:
    >> "%PRECHECK_LOG%.message" echo   %PY_EXE%
    >> "%PRECHECK_LOG%.message" echo.
    >> "%PRECHECK_LOG%.message" echo Preflight output:
    type "%PRECHECK_LOG%" >> "%PRECHECK_LOG%.message"
    >> "%PRECHECK_LOG%.message" echo.
    >> "%PRECHECK_LOG%.message" echo Try:
    >> "%PRECHECK_LOG%.message" echo   python -m pip install -r requirements.txt
    >> "%PRECHECK_LOG%.message" echo   python setup.py
    del "%PRECHECK_LOG%" >nul 2>nul
    set "PRECHECK_LOG=%PRECHECK_LOG%.message"
    call :show_error_from_log
    del "%PRECHECK_LOG%" >nul 2>nul
    exit /b 1
)
del "%PRECHECK_LOG%" >nul 2>nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PYW_EXE%' -ArgumentList '%OVERLAY_PATH%' -WorkingDirectory '%APP_DIR%' -Verb RunAs"
exit /b %ERRORLEVEL%

:try_python_pair
if defined PY_EXE exit /b 0
set "CANDIDATE_DIR=%~1"
if exist "%CANDIDATE_DIR%\python.exe" if exist "%CANDIDATE_DIR%\pythonw.exe" (
    set "PY_EXE=%CANDIDATE_DIR%\python.exe"
    set "PYW_EXE=%CANDIDATE_DIR%\pythonw.exe"
)
exit /b 0

:try_path_python
for /f "delims=" %%P in ('where python.exe 2^>nul') do (
    if not defined PATH_PYTHON_SEEN (
        set "PATH_PYTHON_SEEN=1"
        if exist "%%~dpPpythonw.exe" (
            set "PY_EXE=%%~fP"
            set "PYW_EXE=%%~dpPpythonw.exe"
        )
    )
)
exit /b 0

:show_error_from_log
type "%PRECHECK_LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName System.Windows.Forms; $msg = Get-Content -Raw -LiteralPath '%PRECHECK_LOG%'; [System.Windows.Forms.MessageBox]::Show($msg, 'HeatMap launcher', 'OK', 'Error') | Out-Null"
exit /b 0

@echo off
setlocal EnableExtensions

set "APP_DIR=%~dp0"
set "OVERLAY_PATH=%APP_DIR%overlay.py"
set "SETUP_PATH=%APP_DIR%setup.py"
set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "PY_EXE="
set "PYW_EXE="
set "PRECHECK_LOG="
set "PREFLIGHT_FAILURES=%TEMP%\HeatMap_launcher_%RANDOM%%RANDOM%.txt"
set "CANDIDATE_PY=%APP_DIR%.venv\Scripts\python.exe"
set "CANDIDATE_PYW=%APP_DIR%.venv\Scripts\pythonw.exe"
call :probe_candidate

set "CANDIDATE_PY=%APP_DIR%venv\Scripts\python.exe"
set "CANDIDATE_PYW=%APP_DIR%venv\Scripts\pythonw.exe"
call :probe_candidate
if not defined PY_EXE call :try_path_python

if defined PY_EXE goto candidate_selected
set "PRECHECK_LOG=%PREFLIGHT_FAILURES%"
>> "%PRECHECK_LOG%" echo HeatMap launcher could not find a usable Python interpreter.
>> "%PRECHECK_LOG%" echo.
>> "%PRECHECK_LOG%" echo Every candidate must have adjacent pythonw.exe and pass setup.py --preflight.
>> "%PRECHECK_LOG%" echo Create the project environment and install locked dependencies:
>> "%PRECHECK_LOG%" echo   python -m venv .venv
>> "%PRECHECK_LOG%" echo   .venv\Scripts\python -m pip install -r requirements.txt -c constraints-known-good.txt
call :show_error_from_log
del "%PRECHECK_LOG%" >nul 2>nul
exit /b 1

:candidate_selected
del "%PREFLIGHT_FAILURES%" >nul 2>nul
"%PY_EXE%" -c "import pathlib,sys; sys.exit(0 if 'WARNING:' in pathlib.Path(sys.argv[1]).read_text(encoding='utf-8', errors='replace') else 1)" "%PRECHECK_LOG%" >nul 2>nul
if errorlevel 1 (
    if defined LOCALAPPDATA del "%LOCALAPPDATA%\HeatMap\last_preflight_warning.txt" >nul 2>nul
) else (
    type "%PRECHECK_LOG%"
    if defined LOCALAPPDATA (
        if not exist "%LOCALAPPDATA%\HeatMap" mkdir "%LOCALAPPDATA%\HeatMap" >nul 2>nul
        copy /Y "%PRECHECK_LOG%" "%LOCALAPPDATA%\HeatMap\last_preflight_warning.txt" >nul 2>nul
    )
)
del "%PRECHECK_LOG%" >nul 2>nul

set "HEATMAP_PYW_EXE=%PYW_EXE%"
set "HEATMAP_OVERLAY_PATH=%OVERLAY_PATH%"
set "HEATMAP_APP_DIR=%APP_DIR%"
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "try { $arg = [char]34 + $env:HEATMAP_OVERLAY_PATH + [char]34; Start-Process -FilePath $env:HEATMAP_PYW_EXE -ArgumentList $arg -WorkingDirectory $env:HEATMAP_APP_DIR -Verb RunAs -ErrorAction Stop | Out-Null; exit 0 } catch { Write-Error $_; exit 1 }"
set "LAUNCH_EXIT=%ERRORLEVEL%"
set "HEATMAP_PYW_EXE="
set "HEATMAP_OVERLAY_PATH="
set "HEATMAP_APP_DIR="
exit /b %LAUNCH_EXIT%

:try_path_python
for /f "delims=" %%P in ('"%SystemRoot%\System32\where.exe" python.exe 2^>nul') do (
    if not defined PY_EXE (
        set "CANDIDATE_PY=%%~fP"
        set "CANDIDATE_PYW=%%~dpPpythonw.exe"
        call :probe_candidate
    )
)
exit /b 0

:probe_candidate
if defined PY_EXE exit /b 0
if not exist "%CANDIDATE_PY%" exit /b 0
if not exist "%CANDIDATE_PYW%" exit /b 0
set "CANDIDATE_LOG=%TEMP%\HeatMap_preflight_%RANDOM%%RANDOM%.txt"
"%CANDIDATE_PY%" "%SETUP_PATH%" --preflight > "%CANDIDATE_LOG%" 2>&1
if errorlevel 1 goto candidate_failed
set "PY_EXE=%CANDIDATE_PY%"
set "PYW_EXE=%CANDIDATE_PYW%"
set "PRECHECK_LOG=%CANDIDATE_LOG%"
exit /b 0

:candidate_failed
>> "%PREFLIGHT_FAILURES%" echo Candidate failed preflight:
>> "%PREFLIGHT_FAILURES%" echo   "%CANDIDATE_PY%"
type "%CANDIDATE_LOG%" >> "%PREFLIGHT_FAILURES%"
>> "%PREFLIGHT_FAILURES%" echo.
del "%CANDIDATE_LOG%" >nul 2>nul
exit /b 0

:show_error_from_log
type "%PRECHECK_LOG%"
set "HEATMAP_PRECHECK_LOG=%PRECHECK_LOG%"
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName System.Windows.Forms; $msg = Get-Content -Raw -LiteralPath $env:HEATMAP_PRECHECK_LOG; [System.Windows.Forms.MessageBox]::Show($msg, 'HeatMap launcher', 'OK', 'Error') | Out-Null"
set "HEATMAP_PRECHECK_LOG="
exit /b 0

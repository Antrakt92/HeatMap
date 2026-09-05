@echo off
setlocal EnableExtensions
set "HEATMAP_ACTIVATE_PY=%~dp0.venv\Scripts\pythonw.exe"
set "HEATMAP_ACTIVATE_SCRIPT=%~dp0enable_case_fans.py"
set "HEATMAP_ACTIVATE_DIR=%~dp0"
if not exist "%HEATMAP_ACTIVATE_PY%" (
    echo Create the HeatMap .venv using the README installation steps first.
    pause
    exit /b 1
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "try { $arg = [char]34 + $env:HEATMAP_ACTIVATE_SCRIPT + [char]34; Start-Process -FilePath $env:HEATMAP_ACTIVATE_PY -ArgumentList $arg -WorkingDirectory $env:HEATMAP_ACTIVATE_DIR -WindowStyle Hidden -Verb RunAs -ErrorAction Stop | Out-Null } catch { Write-Error $_; exit 1 }"
exit /b %ERRORLEVEL%

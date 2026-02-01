@echo off
:: Run overlay with administrator privileges and no console window
:: Find pythonw.exe next to the python that's in PATH
for %%I in (python.exe) do set "PYTHON_DIR=%%~dp$PATH:I"
if exist "%PYTHON_DIR%pythonw.exe" (
    powershell -Command "Start-Process '%PYTHON_DIR%pythonw.exe' -ArgumentList '\"%~dp0overlay.py\"' -Verb RunAs"
) else (
    powershell -Command "Start-Process pythonw -ArgumentList '\"%~dp0overlay.py\"' -Verb RunAs"
)

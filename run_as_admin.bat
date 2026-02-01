@echo off
:: Run overlay with administrator privileges and no console window
powershell -Command "Start-Process pythonw -ArgumentList '\"%~dp0overlay.py\"' -Verb RunAs"

@echo off
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -u "%~dp0receber_usb.py"
pause

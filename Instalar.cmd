@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -m venv .venv
goto install
:use_python
python -m venv .venv
:install
if not exist ".venv\Scripts\python.exe" goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Instalacao concluida. Conecte a placa e execute Iniciar.cmd.
pause
exit /b 0
:failed
echo Nao foi possivel concluir. Confira a instalacao do Python e a internet.
pause
exit /b 1

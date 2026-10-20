@echo off
cd /d "%~dp0"
echo Testes locais isolados. Nenhuma mensagem sera enviada.
"%~dp0.venv\Scripts\python.exe" -m unittest discover -s tests -v
pause

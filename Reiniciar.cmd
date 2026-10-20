@echo off
cd /d "%~dp0"
echo Verificando a atualizacao antes de reiniciar...
"%~dp0.venv\Scripts\python.exe" -m unittest discover -s tests -v
if errorlevel 1 goto failed
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; $root=(Get-Location).Path; $launcher=Join-Path $root 'iniciar_completo.py'; $receiver=Join-Path $root 'receber_usb.py'; $connections=Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue; foreach($connection in $connections){$listener=Get-CimInstance Win32_Process -Filter ('ProcessId='+$connection.OwningProcess); if($listener.Name -match '^python(w)?\.exe$' -and (($listener.CommandLine -match 'waitress' -and $listener.CommandLine -match 'server:app') -or $listener.CommandLine.Contains($launcher))){Stop-Process -Id $listener.ProcessId}else{throw 'A porta 5000 pertence a outro programa. Feche-o antes de continuar.'}}; Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -and $_.CommandLine.Contains($receiver)} | ForEach-Object {Stop-Process -Id $_.ProcessId}"
if errorlevel 1 goto failed
"%~dp0.venv\Scripts\python.exe" -u "%~dp0iniciar_completo.py"
:failed
pause

@echo off
rem Put a "DroneVisualizer" shortcut on your Desktop that opens the launcher.
setlocal
set "DIR=%~dp0"
set "PYW=%DIR%.venv\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$lnk = $ws.CreateShortcut([IO.Path]::Combine($ws.SpecialFolders('Desktop'),'DroneVisualizer.lnk'));" ^
  "$lnk.TargetPath = '%PYW%';" ^
  "$lnk.Arguments = '\"%DIR%launcher.pyw\"';" ^
  "$lnk.WorkingDirectory = '%DIR%';" ^
  "$lnk.IconLocation = 'shell32.dll,13';" ^
  "$lnk.Description = 'Start/stop DroneVisualizer';" ^
  "$lnk.Save()"

echo Done - see "DroneVisualizer" on your Desktop.
pause

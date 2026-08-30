@echo off
rem Open the DroneVisualizer launcher window (Start / Stop / Open in browser).
setlocal
set "DIR=%~dp0"
if exist "%DIR%.venv\Scripts\pythonw.exe" (
    start "" "%DIR%.venv\Scripts\pythonw.exe" "%DIR%launcher.pyw"
) else (
    start "" pythonw "%DIR%launcher.pyw"
)

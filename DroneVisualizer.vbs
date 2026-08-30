' Launch the DroneVisualizer window with no console flash at all.
' Same result as DroneVisualizer.bat - pick whichever you prefer.
Dim sh, fso, base, py
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
base = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
py = base & ".venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then py = "pythonw"
sh.CurrentDirectory = base
sh.Run """" & py & """ """ & base & "launcher.pyw""", 0, False

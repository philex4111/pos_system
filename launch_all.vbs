Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
base = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = base

' Prevent double-click / double-launch race (strict lock)
lockPath = base & "\.duka_launch.lock"
On Error Resume Next
If FSO.FileExists(lockPath) Then
  WshShell.Run "http://127.0.0.1:8080", 1, False
  WScript.Quit 0
End If
Set lockFile = FSO.CreateTextFile(lockPath, True)
lockFile.WriteLine "locked"
lockFile.Close
WScript.Sleep 200
On Error GoTo 0

' Stop any old instances (hidden)
WshShell.Run Chr(34) & base & "\stop_all.bat" & Chr(34), 0, True

' Require venv pythonw
If Not FSO.FileExists(base & "\venv\Scripts\python.exe") Then
  WshShell.Popup "Missing venv python.exe. Create venv first in: " & base, 10, "Duka POS", 48
  WScript.Quit 1
End If

' Require .env
If Not FSO.FileExists(base & "\.env") Then
  WshShell.Popup "Missing .env. Copy .env.example to .env and fill keys in: " & base, 12, "Duka POS", 48
  WScript.Quit 1
End If

' Start services hidden
WshShell.Run Chr(34) & base & "\venv\Scripts\python.exe" & Chr(34) & " " & Chr(34) & base & "\dashboard.py" & Chr(34), 0, False
WScript.Sleep 800

WshShell.Run Chr(34) & base & "\venv\Scripts\python.exe" & Chr(34) & " " & Chr(34) & base & "\mpesa_callback.py" & Chr(34), 0, False
WScript.Sleep 800

' ngrok (optional, hidden)
If FSO.FileExists(base & "\ngrok.exe") Then
  WshShell.Run Chr(34) & base & "\ngrok.exe" & Chr(34) & " http 5000", 0, False
  WScript.Sleep 800
End If

WshShell.Run Chr(34) & base & "\venv\Scripts\python.exe" & Chr(34) & " " & Chr(34) & base & "\telegram_bot.py" & Chr(34), 0, False

' Open dashboard
WshShell.Run "http://127.0.0.1:8080", 1, False

' Remove lock
On Error Resume Next
If FSO.FileExists(lockPath) Then FSO.DeleteFile lockPath, True
On Error GoTo 0


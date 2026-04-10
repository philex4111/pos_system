Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
base = FSO.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = base

' Run stop_all.bat hidden (no taskbar window)
WshShell.Run Chr(34) & base & "\stop_all.bat" & Chr(34), 0, True


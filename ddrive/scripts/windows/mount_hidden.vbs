' Starts DDrive in a hidden window (no console stays open). Used by run.bat -> Start.
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "cmd /c """"" & here & "\launch.cmd"" mount""", 0, False

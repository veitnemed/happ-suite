Option Explicit

Dim shell, fso, projectRoot, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
projectRoot = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
shell.CurrentDirectory = projectRoot
command = "pyw.exe -3 -X utf8 -m src.main"
shell.Run command, 0, False

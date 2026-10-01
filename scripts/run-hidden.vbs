Option Explicit

Dim shell, fso, projectRoot, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
projectRoot = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
shell.CurrentDirectory = projectRoot
If fso.FileExists(projectRoot & "\.venv\Scripts\pythonw.exe") Then
    command = Chr(34) & projectRoot & "\.venv\Scripts\pythonw.exe" & Chr(34) & " -X utf8 -m src.start"
Else
    command = "pyw.exe -3 -X utf8 -m src.start"
End If
shell.Run command, 0, False

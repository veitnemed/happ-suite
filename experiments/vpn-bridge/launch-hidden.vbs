Option Explicit

Dim shell, fso, re, scriptDir, role, runId, scenario, psPath, command, scriptPath, result
If WScript.Arguments.Count < 2 Then WScript.Quit 2

role = LCase(WScript.Arguments(0))
runId = LCase(WScript.Arguments(1))
If Not (role = "worker" Or role = "watchdog") Then WScript.Quit 2
Set re = CreateObject("VBScript.RegExp")
re.Pattern = "^[0-9a-f]{32}$"
If Not re.Test(runId) Then WScript.Quit 2

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
psPath = shell.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
scriptPath = scriptDir & "\" & role & ".ps1"
command = Chr(34) & psPath & Chr(34) & " -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & scriptPath & Chr(34) & " -RunId " & runId

If role = "worker" Then
    scenario = "normal"
    If WScript.Arguments.Count > 2 Then scenario = LCase(WScript.Arguments(2))
    If Not (scenario = "normal" Or scenario = "hang") Then WScript.Quit 2
    If scenario = "hang" Then command = command & " -SimulateHang"
End If

result = shell.Run(command, 0, True)
WScript.Quit result

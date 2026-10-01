@echo off
rem Launch the tray process without terminating unrelated Python processes.
start "" "%SystemRoot%\System32\wscript.exe" "%~dp0scripts\run-hidden.vbs"

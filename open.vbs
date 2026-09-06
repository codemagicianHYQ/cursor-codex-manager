' Silent open: no console window. Double-click this (or pin to taskbar).
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.Run "pythonw.exe launch.py", 0, False

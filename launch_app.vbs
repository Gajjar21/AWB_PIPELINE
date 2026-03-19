Set objShell = CreateObject("WScript.Shell")

' Set the path for the Python script and the icon file
pythonScriptPath = "path\to\main.py"
iconPath = "path\to\GJ21-Scan.ico"

' Create a desktop shortcut
shortcutPath = objShell.SpecialFolders("Desktop") & "\MainScript.lnk"
Set objShortcut = objShell.CreateShortcut(shortcutPath)

' Set shortcut properties
objShortcut.TargetPath = "C:\Path\To\Python\python.exe"
objShortcut.Arguments = "" & pythonScriptPath
objShortcut.IconLocation = iconPath
objShortcut.Save

' Run the Python script silently
Set objShell = CreateObject("WScript.Shell")
objShell.Run "C:\Path\To\Python\python.exe " & pythonScriptPath, 0, False
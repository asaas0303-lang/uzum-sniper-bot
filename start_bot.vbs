Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\uzum avto"
WshShell.Run """C:\Users\user\AppData\Local\Programs\Python\Python312\pythonw.exe"" service_runner.py", 0, False

Set oShell = CreateObject("Wscript.Shell")
rc = oShell.Run("cmd.exe /c ""C:\workspace\Data_scrapping_thgingsboard_ml-intern\scripts\run_live_shadow_task.bat""", 0, True)
Wscript.Quit rc

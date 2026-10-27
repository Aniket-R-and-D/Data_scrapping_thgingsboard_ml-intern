@echo off
rem G2 live shadow: one read-only scoring cycle (LOG_ONLY_NO_DISPATCH).
rem Registered as a 15-minute Windows scheduled task; see reports/live_shadow_runbook.md.
setlocal
cd /d "C:\workspace\Data_scrapping_thgingsboard_ml-intern"
if not exist "data\live" mkdir "data\live"
.\.venv\Scripts\python.exe -X utf8 -m src.live.shadow_run --once >> "data\live\shadow_run.log" 2>&1
exit /b %errorlevel%

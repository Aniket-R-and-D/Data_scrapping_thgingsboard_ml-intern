@echo off
rem H1 diagnostic wrapper for the TB_LiveShadow_G2 scheduled task.
rem Separates Task Scheduler's exit code from Python's exit code and captures
rem cmd-level start/exit lines. Python logs go to data\live\shadow_run.log via
rem --log-file (works under pythonw.exe, which has no console).
rem NOTE: a .bat action shows a brief console window while running; this wrapper
rem is for diagnosis. Once the root cause is fixed, the task may return to the
rem direct pythonw.exe action (windowless) documented in reports/live_shadow_runbook.md.
setlocal
cd /d "C:\workspace\Data_scrapping_thgingsboard_ml-intern"
echo [%DATE% %TIME%] task start >> "data\live\task_wrapper.log"
".venv\Scripts\pythonw.exe" -X utf8 -m src.live.shadow_run --once --log-file "data\live\shadow_run.log" --max-backfill-hours 0 >> "data\live\task_wrapper.log" 2>&1
set RC=%ERRORLEVEL%
echo [%DATE% %TIME%] task exit %RC% >> "data\live\task_wrapper.log"
exit /b %RC%

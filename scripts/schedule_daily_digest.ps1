# schedule_daily_digest.ps1
# Automates the ThingsBoard Morning AI Risk Digest via Windows Task Scheduler.
# Runs every day at 08:00 AM IST.

param(
    [string]$Time = "08:00",
    [switch]$RunNow,
    [switch]$Status,
    [switch]$Remove
)

$TaskName = "ThingsBoard_Daily_AI_Digest"
$WorkspaceDir = "C:\workspace\Data_scrapping_thgingsboard_ml-intern"
$PythonExe = "$WorkspaceDir\.venv\Scripts\python.exe"
$ScriptPath = "$WorkspaceDir\send_daily_risk_digest.py"

if ($Status) {
    Write-Host "Checking status for Scheduled Task '$TaskName'..." -ForegroundColor Cyan
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Format-List TaskName, State, Description
    Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue | Format-List LastRunTime, LastTaskResult, NextRunTime
    exit 0
}

if ($Remove) {
    Write-Host "Unregistering Scheduled Task '$TaskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Task '$TaskName' removed successfully." -ForegroundColor Green
    exit 0
}

if ($RunNow) {
    Write-Host "Triggering manual execution of '$TaskName'..." -ForegroundColor Cyan
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Task triggered. Check $WorkspaceDir\logs\daily_risk_digest.log for execution output." -ForegroundColor Green
    exit 0
}

# 1. Verify Prerequisites
if (-not (Test-Path $PythonExe)) {
    Write-Error "Virtual environment python not found at $PythonExe"
    exit 1
}
if (-not (Test-Path $ScriptPath)) {
    Write-Error "Digest script not found at $ScriptPath"
    exit 1
}

# 2. Build Task Action & Trigger
$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "$ScriptPath" -WorkingDirectory $WorkspaceDir
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)

# 3. Register Task (Runs under current user context without requiring admin elevation if user privilege allows)
Write-Host "Registering Scheduled Task '$TaskName' to run daily at $Time..." -ForegroundColor Cyan

try {
    # Remove existing task if present to update settings cleanly
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Runs ThingsBoard Daily AI Anomaly and Outage Risk Operations Digest every morning at $Time."
    Write-Host "Scheduled Task '$TaskName' registered successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Useful Commands:" -ForegroundColor White
    Write-Host "  To view status:       .\scripts\schedule_daily_digest.ps1 -Status" -ForegroundColor DarkGray
    Write-Host "  To test run now:      .\scripts\schedule_daily_digest.ps1 -RunNow" -ForegroundColor DarkGray
    Write-Host "  To remove task:       .\scripts\schedule_daily_digest.ps1 -Remove" -ForegroundColor DarkGray
} catch {
    Write-Error "Failed to register Scheduled Task: $_"
    exit 1
}

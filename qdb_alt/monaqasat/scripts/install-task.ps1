# Register a nightly harvest that continues until every section is complete.
# Run this once, from an elevated PowerShell if you want it to run while
# logged out:
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-task.ps1
#
# Closing the laptop lid usually sleeps Windows, which stops the job. Set
# "When I close the lid" to "Do nothing" while plugged in, or run this on a
# VM / always-on PC instead. Task Scheduler will wake the machine if sleep
# is allowed to be interrupted.

param(
    [string]$Time = "01:00",
    [double]$MaxHours = 6
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "harvest.ps1"
if (-not (Test-Path $script)) {
    throw "missing $script"
}

$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -MaxHours $MaxHours"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours ([Math]::Ceiling($MaxHours) + 1))

$taskName = "QDB Monaqasat harvest"
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Force | Out-Null

Write-Host "Registered scheduled task '$taskName' daily at $Time."
Write-Host "Each run lasts at most $MaxHours hours and then picks up the next night."
Write-Host "Progress:  python -m monaqasat status"
Write-Host "Logs:      $Root\logs\"
Write-Host ""
Write-Host "Laptop lid: Power Options -> Choose what closing the lid does -> Do nothing (plugged in)."
Write-Host "Or run this on a VM that stays on. Sleep stops the harvest."

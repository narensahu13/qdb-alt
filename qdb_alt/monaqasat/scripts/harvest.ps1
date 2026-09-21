# Resume-safe harvest. Safe to run daily; each page is committed before the next.
# Usage:  powershell -File scripts\harvest.ps1
#         powershell -File scripts\harvest.ps1 -MaxHours 6

param(
    [double]$MaxHours = 6,
    [string]$Db = "monaqasat.db"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$logDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("harvest-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

function Write-Log([string]$msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $log -Value $line
    Write-Host $line
}

Write-Log "harvest start  db=$Db  max-hours=$MaxHours  cwd=$Root"

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Log "python is not on PATH"
    exit 1
}

& python -m monaqasat harvest --db $Db --max-hours $MaxHours *>> $log
$code = $LASTEXITCODE
& python -m monaqasat status --db $Db *>> $log
Write-Log "harvest exit $code"
exit $code

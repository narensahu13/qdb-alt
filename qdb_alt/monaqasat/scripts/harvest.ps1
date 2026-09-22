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
    [System.IO.File]::AppendAllText($log, $line + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    Write-Host $line
}

Write-Log "harvest start  db=$Db  max-hours=$MaxHours  cwd=$Root"

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Log "python is not on PATH"
    exit 1
}

$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"

function Invoke-LoggedPython([string]$arguments) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $py.Source
    $psi.Arguments = $arguments
    $psi.WorkingDirectory = $Root
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $psi.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    $proc = [System.Diagnostics.Process]::Start($psi)
    while (-not $proc.StandardOutput.EndOfStream) {
        $line = $proc.StandardOutput.ReadLine()
        if ($null -ne $line) { Write-Log $line }
    }
    $err = $proc.StandardError.ReadToEnd()
    if ($err) { Write-Log $err.TrimEnd() }
    $proc.WaitForExit()
    return $proc.ExitCode
}

$code = Invoke-LoggedPython "-m monaqasat harvest --db $Db --max-hours $MaxHours"
Invoke-LoggedPython "-m monaqasat status --db $Db" | Out-Null
Write-Log "harvest exit $code"
exit $code

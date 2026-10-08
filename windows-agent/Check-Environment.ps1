[CmdletBinding()]
param(
    [string]$ConfigPath = '',
    [string]$Python = '',
    [string]$OutputDirectory = '',
    [switch]$Pause
)
$ErrorActionPreference = 'Stop'
$exitCode = 1
if (-not $ConfigPath) { $ConfigPath = Join-Path $PSScriptRoot 'config.json' }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $PSScriptRoot 'diagnostics' }
try {
    Write-Host 'Checking environment. Services and deployment tasks will not be changed.'
    if (-not $Python -and (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        try {
            $config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $applications = @($config.applications.PSObject.Properties)
            if ($applications.Count -eq 1) { $Python = [string]$applications[0].Value.Python }
        } catch { Write-Host 'Cannot select Python from config; trying installed Python. The report will check JSON separately.' }
    }
    if (-not $Python -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $Python = $candidate }
        else {
            $command = Get-Command python.exe -ErrorAction SilentlyContinue
            if ($command) { $Python = $command.Source }
        }
    }
    if (-not $Python -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw 'Python executable not found. Install Python 3.13 or run Check-Environment.ps1 -Python C:/path/to/python.exe.'
    }
    foreach ($file in @('diagnose_environment.py', 'agent_check.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $file) -PathType Leaf)) {
            throw ('Missing diagnostic file: ' + $file + '. Download the complete environment checker.')
        }
    }
    & $Python (Join-Path $PSScriptRoot 'diagnose_environment.py') --config $ConfigPath --output $OutputDirectory
    $exitCode = $LASTEXITCODE
} catch {
    $message = '[FAIL] Environment checker bootstrap: ' + $_.Exception.Message
    Write-Host $message -ForegroundColor Red
    try {
        New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
        $report = Join-Path $OutputDirectory ('environment-bootstrap-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt')
        [IO.File]::WriteAllText($report, $message, (New-Object Text.UTF8Encoding($false)))
        Write-Host ('Report: ' + $report)
    } catch { Write-Host 'Cannot write the report. Run in a writable directory or set -OutputDirectory.' }
} finally {
    if ($Pause) { [void](Read-Host 'Press Enter to close') }
}
exit $exitCode

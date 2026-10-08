[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [ValidateSet('deploy','rollback')][string]$Action = 'deploy',
    [string]$Package,
    [string]$ExpectedSha256,
    [string]$VerifiedDirectory,
    [string]$CancellationFile
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
$root = [IO.Path]::GetFullPath($config.InstallRoot)
if (-not [IO.Path]::IsPathRooted($config.InstallRoot) -or $root.TrimEnd('\') -eq [IO.Path]::GetPathRoot($root).TrimEnd('\')) {
    throw 'InstallRoot must be an application directory, not a drive root.'
}
if ($config.PSObject.Properties.Name -contains 'Environment') {
    foreach ($item in $config.Environment.PSObject.Properties) {
        [Environment]::SetEnvironmentVariable($item.Name, [string]$item.Value, 'Process')
    }
}
$deploy = Join-Path $root '.deploy'
New-Item -ItemType Directory -Force $deploy | Out-Null
$historyPath = Join-Path $deploy 'agent-history.json'
$activePath = Join-Path $deploy 'active-release.json'
$xmlPath = Join-Path $deploy 'service\python-mt5-http.xml'
$oldActive = if (Test-Path -LiteralPath $activePath) { [IO.File]::ReadAllText($activePath) } else { $null }
$oldXml = if (Test-Path -LiteralPath $xmlPath) { [IO.File]::ReadAllText($xmlPath) } else { $null }
$oldService = Get-Service -Name 'python-mt5-http' -ErrorAction SilentlyContinue
$wrapper = Join-Path $deploy 'service\python-mt5-http.exe'
$serviceInfo = Get-CimInstance Win32_Service -Filter "Name='python-mt5-http'"
if ($serviceInfo -and $serviceInfo.PathName.Trim('"') -ne $wrapper) { throw 'HTTP service belongs to a different install directory.' }
$logon = if ($serviceInfo) { $serviceInfo.StartName } else { 'LocalSystem' }
if ($logon -eq 'LocalSystem') { $serviceSid = 'S-1-5-18' }
else {
    if ($logon.StartsWith('.\')) { $logon = $env:COMPUTERNAME + $logon.Substring(1) }
    $serviceSid = ([Security.Principal.NTAccount]::new($logon)).Translate([Security.Principal.SecurityIdentifier]).Value
}
if ([Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne $serviceSid) {
    throw 'Agent and business service must use the same logon identity for MT5 credential preflight. Initialize the business service and configure its logon account first.'
}
$wasRunning = $null -ne $oldService -and $oldService.Status -eq 'Running'
$oldRelease = if ($oldActive) { ($oldActive | ConvertFrom-Json).releasePath } else { $null }
$timeout = [int]$config.StartupTimeoutSeconds
if ($timeout -lt 30 -or $timeout -gt 1200) { throw 'StartupTimeoutSeconds must be between 30 and 1200.' }
$envPath = Join-Path $root '.env'
$envSupplied = $null -ne $config.PSObject.Properties['EnvContent'] -and -not [string]::IsNullOrWhiteSpace([string]$config.EnvContent)
$envExisted = $envSupplied -and (Test-Path -LiteralPath $envPath)
$oldEnv = $null
if ($envExisted) { $oldEnv = [IO.File]::ReadAllBytes($envPath) }
$envApplied = $false
function Write-EnvironmentFile([byte[]]$bytes) {
    $temporary = $envPath + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllBytes($temporary, $bytes)
        if (Test-Path -LiteralPath $envPath) {
            # File.Replace keeps the existing file's access control on Windows.
            [IO.File]::Replace($temporary, $envPath, $null)
        } else { [IO.File]::Move($temporary, $envPath) }
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}
function Apply-ConfiguredEnvironment {
    if ($envSupplied) {
        Write-EnvironmentFile -bytes ((New-Object Text.UTF8Encoding($false)).GetBytes([string]$config.EnvContent))
        $script:envApplied = $true
        Write-Output 'Selected deployment configuration .env applied; values are not logged.'
    }
}
function Restore-EnvironmentFile {
    if ($envApplied) {
        if (-not $envExisted) { Remove-Item -LiteralPath $envPath -Force }
        else { Write-EnvironmentFile -bytes $oldEnv }
        $script:envApplied = $false
        Write-Output 'Previous .env restored.'
    }
}
try {
    # Existing services keep their .env until cutover. Explicit process variables
    # let the preparation checks use the selected configuration in the meantime.
    if ($envSupplied -and -not (Test-Path -LiteralPath $envPath)) { Apply-ConfiguredEnvironment }
    if ($Action -eq 'deploy') {
        $expand = Join-Path $VerifiedDirectory 'deploy\windows\Expand-Release.ps1'
        Write-Output 'Preparing isolated release and locked Windows dependencies'
        & $expand -Package $Package -ExpectedSha256 $ExpectedSha256 -InstallRoot $root -Python $config.Python -Uv $config.Uv
        $release = (Get-Content -LiteralPath (Join-Path $deploy 'prepared-release.txt') -Raw).Trim()
    } else {
        if (-not (Test-Path -LiteralPath $historyPath)) { throw 'No previous successful release is available.' }
        $history = Get-Content -LiteralPath $historyPath -Raw | ConvertFrom-Json
        if (-not $history.previous) { throw 'No previous successful release is available.' }
        if ($oldRelease -ne $history.current) { throw 'Active release changed outside Agent; inspect history before rollback.' }
        $release = $history.previous
    }
    $release = (Resolve-Path -LiteralPath $release).Path
    $releasePrefix = (Join-Path $deploy 'releases').TrimEnd('\') + '\'
    if (-not $release.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Release path is outside the application release directory.' }
    $runtime = Join-Path $release '.venv\Scripts\python.exe'
    Push-Location $root
    try {
        $settingsText = & $runtime (Join-Path $PSScriptRoot 'inspect_application.py') --release-root $release --config (Join-Path $deploy 'bootstrap-http.yaml')
        if ($LASTEXITCODE -ne 0) { throw 'Runtime preflight failed before cutover.' }
        $settings = $settingsText | ConvertFrom-Json
        $capabilities = $settings
        if ($settings.journalPath) {
            $journalPath = [IO.Path]::GetFullPath($settings.journalPath)
            if ($journalPath.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Manager command journal must be outside versioned release directories.'
            }
        }
    } finally { Pop-Location }
    if ($CancellationFile -and (Test-Path -LiteralPath $CancellationFile)) { throw 'Release cancelled before cutover.' }
} catch {
    $preparationFailure = $_
    try { Restore-EnvironmentFile }
    catch { throw 'Preparation failed and .env restoration failed. Inspect the application directory before retrying.' }
    throw $preparationFailure
}
$installedNow = $false
$cutoverStarted = $false
try {
    Write-Output "Activating $Action release through WinSW"
    if ($oldService) {
        $cutoverStarted = $true
        Stop-Service -Name 'python-mt5-http'
        (Get-Service 'python-mt5-http').WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))
    }
    if (@(Get-NetTCPConnection -LocalPort $settings.port -State Listen -ErrorAction SilentlyContinue).Count -gt 0) {
        throw 'HTTP port is occupied; unrelated processes will not be stopped.'
    }
    $cutoverStarted = $true
    & (Join-Path $release 'deploy\windows\Stop-PreviousService.ps1') -InstallRoot $root
    Apply-ConfiguredEnvironment
    New-Item -ItemType Directory -Force (Split-Path -Parent $wrapper),(Join-Path $deploy 'logs') | Out-Null
    if (-not (Test-Path -LiteralPath $wrapper)) {
        Copy-Item -LiteralPath $config.ServiceWrapper -Destination $wrapper
    }
    $escape = { param([string]$value) [Security.SecurityElement]::Escape($value) }
    $environmentXml = ''
    if ($config.PSObject.Properties.Name -contains 'Environment') {
        foreach ($item in $config.Environment.PSObject.Properties) {
            $environmentXml += '<env name="' + (& $escape $item.Name) + '" value="' + (& $escape ([string]$item.Value)) + '" />' + "`r`n"
        }
    }
    $xml = @"
<service>
  <id>python-mt5-http</id>
  <name>Trader MT5 HTTP Service</name>
  <description>MT5 HTTP queries, streaming and Manager API hosted by the Windows deployment agent.</description>
  <executable>$(& $escape $runtime)</executable>
  <arguments>-m python_mt5_sidecar http-server --config &quot;$(& $escape (Join-Path $deploy 'bootstrap-http.yaml'))&quot;</arguments>
  <workingdirectory>$(& $escape $root)</workingdirectory>
  <env name="PYTHONUNBUFFERED" value="1" />
  $environmentXml
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <onfailure action="restart" delay="1 min" />
  <stoptimeout>30 sec</stoptimeout>
  <logpath>$(& $escape (Join-Path $deploy 'logs'))</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>7</keepFiles></log>
</service>
"@
    [IO.File]::WriteAllText($xmlPath, $xml, (New-Object Text.UTF8Encoding($false)))
    if (-not $oldService) {
        & $wrapper install
        if ($LASTEXITCODE -ne 0) { throw 'WinSW service installation failed.' }
        $installedNow = $true
    } else {
        # The existing wrapper reads the new XML on start; older WinSW has no refresh.
        Set-Service -Name 'python-mt5-http' -StartupType Automatic
    }
    Start-Service -Name 'python-mt5-http'
    $deadline = [DateTime]::UtcNow.AddSeconds($timeout)
    $healthy = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $ready = Invoke-RestMethod -Uri ($settings.healthUrl + '/health/ready') -TimeoutSec 5
            if ($ready.status -ne 'UP') { throw 'Query service is not ready.' }
            if ($capabilities.streaming) {
                $stream = Invoke-RestMethod -Uri ($settings.healthUrl + '/health/streaming') -TimeoutSec 5
                if (-not $stream.enabled -or $stream.status -ne 'LIVE') { throw 'Streaming is not ready.' }
            }
            if ($capabilities.manager) {
                $manager = Invoke-RestMethod -Uri ($settings.healthUrl + '/api/v1/manager/health') -TimeoutSec 30
                if (-not $manager.ready) { throw 'Manager SDK is not ready.' }
            }
            $healthy = $true
            break
        } catch { Start-Sleep -Seconds 2 }
    }
    if (-not $healthy) { throw 'Full application health check timed out.' }
    Set-Service -Name 'python-mt5-http' -StartupType Automatic
    [ordered]@{releasePath=$release; service='python-mt5-http'; port=$settings.port; healthUrl=$settings.healthUrl; activatedAtUtc=[DateTime]::UtcNow.ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath ($activePath + '.tmp') -Encoding UTF8
    Move-Item -LiteralPath ($activePath + '.tmp') -Destination $activePath -Force
    [ordered]@{current=$release; previous=$oldRelease; activatedAtUtc=[DateTime]::UtcNow.ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath ($historyPath + '.tmp') -Encoding UTF8
    Move-Item -LiteralPath ($historyPath + '.tmp') -Destination $historyPath -Force
    Write-Output "SUCCESS: $Action complete; queries, enabled streams and Manager connections verified. Persistent command journal was preserved."
} catch {
    $failure = $_
    if (-not $cutoverStarted) {
        try { Restore-EnvironmentFile }
        catch { throw 'Deployment failed before cutover and .env restoration failed. Inspect the application directory.' }
        throw $failure
    }
    try {
        Stop-Service -Name 'python-mt5-http' -ErrorAction SilentlyContinue
        $service = Get-Service -Name 'python-mt5-http' -ErrorAction SilentlyContinue
        if ($service) { $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60)) }
        if ($installedNow) {
            & $wrapper uninstall
            if ($LASTEXITCODE -ne 0) { throw 'Failed to uninstall unsuccessful service.' }
        }
        Restore-EnvironmentFile
        if ($oldXml) {
            [IO.File]::WriteAllText($xmlPath, $oldXml, (New-Object Text.UTF8Encoding($false)))
            if ($oldService) {
                # Restarting uses the restored XML and keeps the service logon account.
                $priorMode = switch ($serviceInfo.StartMode) { 'Auto' { 'Automatic' } 'Disabled' { 'Disabled' } default { 'Manual' } }
                Set-Service -Name 'python-mt5-http' -StartupType $priorMode
            }
        }
        if ($oldActive) { [IO.File]::WriteAllText($activePath, $oldActive, (New-Object Text.UTF8Encoding($false))) }
        elseif (Test-Path -LiteralPath $activePath) { Remove-Item -LiteralPath $activePath }
        if ($wasRunning) {
            Start-Service -Name 'python-mt5-http'
            $restoreHealthUrl = $settings.healthUrl
            if ($oldActive) {
                $previousActive = $oldActive | ConvertFrom-Json
                if ($null -ne $previousActive.PSObject.Properties['healthUrl']) { $restoreHealthUrl = $previousActive.healthUrl }
                elseif ($null -ne $previousActive.PSObject.Properties['port']) { $restoreHealthUrl = 'http://127.0.0.1:' + $previousActive.port }
            }
            $restoreDeadline = [DateTime]::UtcNow.AddSeconds($timeout)
            $restored = $false
            while ([DateTime]::UtcNow -lt $restoreDeadline) {
                try {
                    $health = Invoke-RestMethod -Uri ($restoreHealthUrl + '/health/ready') -TimeoutSec 5
                    if ($health.status -eq 'UP') { $restored = $true; break }
                } catch {}
                Start-Sleep -Seconds 2
            }
            if (-not $restored) { throw 'Previous service failed readiness check after rollback.' }
            Write-Output 'Previous HTTP service restored and readiness verified.'
        } elseif (-not $oldXml) {
            $previousState = Join-Path $deploy 'state\previous-service.json'
            if (Test-Path -LiteralPath $previousState) {
                $previous = Get-Content -LiteralPath $previousState -Raw | ConvertFrom-Json
                $previousService = Get-CimInstance Win32_Service -Filter "Name='python-mt5-sidecar'"
                if ($previous.name -ne 'python-mt5-sidecar' -or $previousService.PathName.Trim('"') -ne $previous.executable) {
                    throw 'Previous collector identity changed; manual recovery required.'
                }
                $mode = switch ($previous.startMode) { 'Auto' { 'Automatic' } 'Disabled' { 'Disabled' } default { 'Manual' } }
                Set-Service -Name 'python-mt5-sidecar' -StartupType $mode
                if ($previous.wasRunning) { Start-Service -Name 'python-mt5-sidecar' }
                Write-Output 'Previous collector state restored.'
            }
        }
    } catch { Write-Output 'ROLLBACK FAILED: inspect WinSW service and local logs before retrying.' }
    throw $failure
}

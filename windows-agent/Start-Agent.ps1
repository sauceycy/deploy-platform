[CmdletBinding()]
param(
    [string]$ConfigPath = '',
    [string]$Python = '',
    [string]$WinSW = '',
    [switch]$Pause,
    [switch]$NoElevation,
    [switch]$SkipPlatformCheck
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$serviceName = 'deploy-platform-windows-agent'
$stage = 'Administrator privileges'
$config = $null
$logDirectory = Join-Path $PSScriptRoot 'logs'
$exitCode = 0

function Quote-Path([string]$value) {
    if ($value.Contains('"')) { throw 'A path must not contain a double quote.' }
    return '"' + $value + '"'
}

function Protect-Output([string]$value) {
    $secrets = @($env:WINDOWS_AGENT_TOKEN, $env:CF_ACCESS_CLIENT_ID, $env:CF_ACCESS_CLIENT_SECRET)
    if ($null -ne $script:config) {
        $tokenProperty = $script:config.PSObject.Properties['agentToken']
        if ($null -ne $tokenProperty) { $secrets += [string]$tokenProperty.Value }
        $applicationsProperty = $script:config.PSObject.Properties['applications']
        if ($null -ne $applicationsProperty -and $null -ne $applicationsProperty.Value) {
            foreach ($application in $applicationsProperty.Value.PSObject.Properties) {
                if ($null -eq $application.Value) { continue }
                $environmentProperty = $application.Value.PSObject.Properties['Environment']
                if ($null -ne $environmentProperty -and $null -ne $environmentProperty.Value) {
                    foreach ($item in $environmentProperty.Value.PSObject.Properties) { $secrets += [string]$item.Value }
                }
            }
        }
    }
    foreach ($secret in $secrets) {
        if ($null -ne $secret -and $secret.Length -ge 4) { $value = $value.Replace($secret, '***') }
    }
    if ($value -match 'input_value=|credentialRef|(?:password|secret|token)\s*[:=]') { return '[sensitive configuration output omitted]' }
    return $value
}

function Show-Logs {
    $files = @(Get-ChildItem -LiteralPath $script:logDirectory -Filter '*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 3)
    if ($files.Count -eq 0) {
        Write-Host ('No WinSW logs found. Check the service logon account can access Python, this folder and stateDirectory.') -ForegroundColor Yellow
        return
    }
    foreach ($file in $files) {
        Write-Host ('--- ' + $file.FullName + ' ---')
        Get-Content -LiteralPath $file.FullName -Tail 30 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host (Protect-Output ([string]$_)) }
    }
}

try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        if ($NoElevation) { throw 'Administrator permission was not granted. Right-click Start-Agent.cmd and run as Administrator.' }
        $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Quote-Path $PSCommandPath), '-NoElevation')
        foreach ($parameter in @('ConfigPath', 'Python', 'WinSW')) {
            $value = Get-Variable -Name $parameter -ValueOnly
            if ($value) {
                $resolved = [IO.Path]::GetFullPath($value)
                $arguments += '-' + $parameter
                $arguments += Quote-Path $resolved
            }
        }
        if ($Pause) { $arguments += '-Pause' }
        if ($SkipPlatformCheck) { $arguments += '-SkipPlatformCheck' }
        $elevated = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $arguments -Verb RunAs -Wait -PassThru
        $Pause = $false
        exit $elevated.ExitCode
    }

    $stage = 'Read config.json'
    if (-not $ConfigPath) { $ConfigPath = Join-Path $PSScriptRoot 'config.json' }
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { throw 'config.json not found. Configure config.example.json as config.json in this Agent folder.' }
    $ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
    try { $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json }
    catch { throw 'config.json is not valid JSON. Use paths like C:/Users/Administrator/... and check commas and quotes.' }
    if ($null -eq $config -or $null -eq $config.PSObject.Properties['applications'] -or $null -eq $config.applications) { throw 'config.json is missing applications.' }
    $applications = @($config.applications.PSObject.Properties)
    if ($applications.Count -ne 1) { throw 'config.json must have exactly one configured application.' }
    $application = $applications[0].Value
    if ($null -eq $application -or $application -isnot [PSCustomObject]) { throw 'Application settings must be a JSON object.' }

    $stage = 'Python and WinSW paths'
    if (-not $Python) {
        if ($null -ne $application.PSObject.Properties['Python']) { $Python = [string]$application.Python }
    }
    if (-not $WinSW) {
        if ($null -ne $application.PSObject.Properties['ServiceWrapper']) { $WinSW = [string]$application.ServiceWrapper }
    }
    if (-not $Python -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Python executable not found. Set applications.<app>.Python to the installed Python 3.13 executable.' }
    if (-not $WinSW -or -not (Test-Path -LiteralPath $WinSW -PathType Leaf)) { throw 'WinSW executable not found. Set applications.<app>.ServiceWrapper to WinSW-x64.exe.' }
    $Python = (Resolve-Path -LiteralPath $Python).Path
    $WinSW = (Resolve-Path -LiteralPath $WinSW).Path
    foreach ($file in @('windows_agent.py', 'agent_check.py')) {
        if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot $file))) { throw ('Missing Agent file: ' + $file + '. Extract the complete windows-agent directory.') }
    }

    $stage = 'Python runtime, JSON and state directory'
    $runtimeText = & $Python (Join-Path $PSScriptRoot 'agent_check.py') --config $ConfigPath --mode runtime 2>&1
    if ($LASTEXITCODE -ne 0) { throw (($runtimeText | ForEach-Object { Protect-Output ([string]$_) }) -join [Environment]::NewLine) }
    $runtime = ($runtimeText -join [Environment]::NewLine) | ConvertFrom-Json
    Write-Host ('[OK] Python ' + $runtime.pythonVersion + '; JSON configuration and state directory checked.') -ForegroundColor Green

    if (-not $SkipPlatformCheck) {
        $stage = 'Platform URL, server registration and Agent Token'
        $platformText = & $Python (Join-Path $PSScriptRoot 'agent_check.py') --config $ConfigPath --mode platform 2>&1
        if ($LASTEXITCODE -ne 0) { throw (($platformText | ForEach-Object { Protect-Output ([string]$_) }) -join [Environment]::NewLine) }
        Write-Host '[OK] Platform accepted the Agent heartbeat.' -ForegroundColor Green
    }

    $stage = 'Existing Agent service'
    $existing = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
    if ($existing) {
        if ($existing.PathName -match '^"([^"]+)"') { $oldExecutable = $Matches[1] }
        elseif ($existing.PathName -match '^(.+?\.exe)(?:\s|$)') { $oldExecutable = $Matches[1] }
        else { throw 'Existing Agent service executable path is invalid; inspect it in Windows Services.' }
        if ([IO.Path]::GetFileName($oldExecutable) -ne 'deploy-platform-windows-agent.exe') { throw 'The existing service name belongs to another executable; refusing to change it.' }
        if ($existing.State -ne 'Stopped') {
            $runtimeText = & $Python (Join-Path $PSScriptRoot 'agent_check.py') --config $ConfigPath --mode runtime 2>&1
            if ($LASTEXITCODE -ne 0) { throw (($runtimeText | ForEach-Object { Protect-Output ([string]$_) }) -join [Environment]::NewLine) }
            $runtime = ($runtimeText -join [Environment]::NewLine) | ConvertFrom-Json
            if ($runtime.runningTasks -gt 0) { throw 'An unfinished deployment is recorded. Wait for it to complete before restarting Agent; inspect stateDirectory logs if it was interrupted.' }
            Stop-Service -Name $serviceName
            (Get-Service $serviceName).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(40))
        }
    }

    $stage = 'Generate WinSW service configuration'
    $wrapper = Join-Path $PSScriptRoot 'deploy-platform-windows-agent.exe'
    if (-not [string]::Equals($WinSW, $wrapper, [StringComparison]::OrdinalIgnoreCase)) { Copy-Item -LiteralPath $WinSW -Destination $wrapper -Force }
    New-Item -ItemType Directory -Force $logDirectory | Out-Null
    $escape = { param([string]$value) [Security.SecurityElement]::Escape($value) }
    $xml = @"
<service>
  <id>deploy-platform-windows-agent</id>
  <name>Deploy Platform Windows Agent</name>
  <description>Receives Windows release tasks and manages MT5 Sidecar deployment and rollback.</description>
  <executable>$(& $escape $Python)</executable>
  <arguments>&quot;$(& $escape (Join-Path $PSScriptRoot 'windows_agent.py'))&quot; --config &quot;$(& $escape $ConfigPath)&quot;</arguments>
  <workingdirectory>$(& $escape $PSScriptRoot)</workingdirectory>
  <env name="PYTHONUNBUFFERED" value="1" />
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <onfailure action="restart" delay="30 sec" />
  <stoptimeout>40 sec</stoptimeout>
  <logpath>$(& $escape $logDirectory)</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>7</keepFiles></log>
</service>
"@
    [xml]$validatedXml = $xml
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot 'deploy-platform-windows-agent.xml'), $validatedXml.OuterXml, (New-Object Text.UTF8Encoding($false)))

    $stage = 'Register or repair Agent service'
    if ($existing) {
        # Updating ImagePath preserves the existing service logon identity and password.
        $changed = Invoke-CimMethod -InputObject $existing -MethodName Change -Arguments @{PathName=(Quote-Path $wrapper); StartMode='Automatic'}
        if ($changed.ReturnValue -ne 0) { throw ('Windows service update failed; SCM return code ' + $changed.ReturnValue) }
        # WinSW reads the updated adjacent XML when the stopped service starts.
        Write-Host ('[OK] Existing Agent registration updated; service logon account kept: ' + $existing.StartName) -ForegroundColor Green
    } else {
        & $wrapper install
        if ($LASTEXITCODE -ne 0) { throw 'WinSW Agent installation failed; see the wrapper output above.' }
        Write-Host '[OK] Agent service installed using default LocalSystem logon.' -ForegroundColor Green
    }

    $stage = 'Start Agent and check for immediate process exit'
    Start-Service -Name $serviceName
    (Get-Service $serviceName).WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    $initial = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
    $agentProcessId = $initial.ProcessId
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        Start-Sleep -Seconds 1
        $current = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
        if ($current.State -ne 'Running' -or $current.ProcessId -ne $agentProcessId -or $agentProcessId -eq 0) {
            throw ('Agent exited or restarted after launch. State=' + $current.State + '; ExitCode=' + $current.ExitCode)
        }
    }
    Write-Host '[SUCCESS] Windows Agent has stayed running for 10 seconds.' -ForegroundColor Green
    Write-Host ('Service account: ' + $current.StartName)
    Write-Host ('Logs: ' + $logDirectory)
    if ($SkipPlatformCheck) { Write-Host 'Platform check was skipped. Running service does not confirm platform connectivity.' -ForegroundColor Yellow }
    else { Write-Host 'Refresh platform Cluster Management to verify the continuing Agent heartbeat.' }
    Write-Host 'Sidecar is not needed to start Agent. Configure MT5 credentials and the business service logon identity before the first deployment.'
} catch {
    $exitCode = 1
    $failure = $_.Exception
    Write-Host ('[FAILED] Stage: ' + $stage) -ForegroundColor Red
    Write-Host (Protect-Output $failure.Message) -ForegroundColor Red
    while ($null -ne $failure.InnerException) {
        $failure = $failure.InnerException
        Write-Host (Protect-Output $failure.Message) -ForegroundColor Red
    }
    if ($stage -eq 'Start Agent and check for immediate process exit') {
        try {
            $failedService = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
            if ($null -ne $failedService) {
                Write-Host ('Service state=' + $failedService.State + '; Windows ExitCode=' + $failedService.ExitCode + '; ServiceSpecificExitCode=' + $failedService.ServiceSpecificExitCode)
            }
        } catch { Write-Host 'Cannot read service state. Check Windows Services or Event Viewer.' }
    }
    if ($stage -eq 'Platform URL, server registration and Agent Token') {
        Write-Host 'Platform preflight failed; the service startup step was not reached.' -ForegroundColor Yellow
        Write-Host 'Check the platform domain proxy rules for /api/windows-agent/*. Skipping this check cannot restore platform connectivity.'
    } elseif ($stage -in @('Existing Agent service', 'Generate WinSW service configuration', 'Register or repair Agent service', 'Start Agent and check for immediate process exit')) {
        try { Show-Logs }
        catch { Write-Host 'Cannot read WinSW logs. Check folder permissions or Windows Event Viewer.' -ForegroundColor Yellow }
        if ($stage -eq 'Start Agent and check for immediate process exit') {
            Write-Host 'Access denied: check service account access to Python, Agent folder and stateDirectory.'
            Write-Host 'File lock error: another Agent instance may be running; do not delete its lock or database.'
            Write-Host 'Error 1069: check the service account password and Log on as a service permission.'
        }
    }
} finally {
    if ($Pause) { [void](Read-Host 'Press Enter to close') }
}
exit $exitCode

[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$WinSW,
    [string]$ConfigPath = '',
    [switch]$Start
)
$ErrorActionPreference = 'Stop'
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $PSScriptRoot 'config.json'
}
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run as Administrator.' }
$Python = (Resolve-Path -LiteralPath $Python).Path
$WinSW = (Resolve-Path -LiteralPath $WinSW).Path
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
& $Python -c "import sys; assert sys.version_info >= (3, 13), 'Python 3.13 or newer required for Agent'"
if ($LASTEXITCODE -ne 0) { throw 'Agent Python runtime check failed.' }
if (Get-Service -Name 'deploy-platform-windows-agent' -ErrorAction SilentlyContinue) { throw 'Agent service is already installed; stop it before updating files.' }
$wrapper = Join-Path $PSScriptRoot 'deploy-platform-windows-agent.exe'
Copy-Item -LiteralPath $WinSW -Destination $wrapper
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
  <logpath>$(& $escape (Join-Path $PSScriptRoot 'logs'))</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>7</keepFiles></log>
</service>
"@
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'deploy-platform-windows-agent.xml'), $xml, (New-Object Text.UTF8Encoding($false)))
& $wrapper install
if ($LASTEXITCODE -ne 0) { throw 'WinSW Agent installation failed.' }
if ($Start) { Start-Service -Name 'deploy-platform-windows-agent' }
Write-Output 'Agent installed. Configure the service logon identity and application credentials before starting.'

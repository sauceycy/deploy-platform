[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$WinSW
)
$ErrorActionPreference = 'Stop'
if (Get-Service -Name 'python-mt5-http' -ErrorAction SilentlyContinue) { throw 'Business service is already installed; keep its existing logon identity.' }
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run as Administrator.' }
$root = [IO.Path]::GetFullPath($InstallRoot)
$Python = (Resolve-Path -LiteralPath $Python).Path
$WinSW = (Resolve-Path -LiteralPath $WinSW).Path
$directory = Join-Path $root '.deploy\service'
New-Item -ItemType Directory -Force $directory | Out-Null
$wrapper = Join-Path $directory 'python-mt5-http.exe'
Copy-Item -LiteralPath $WinSW -Destination $wrapper
$escape = { param([string]$value) [Security.SecurityElement]::Escape($value) }
$xml = @"
<service>
  <id>python-mt5-http</id>
  <name>Trader MT5 HTTP Service</name>
  <description>MT5 HTTP queries, streaming and Manager API hosted by the Windows deployment agent.</description>
  <executable>$(& $escape $Python)</executable>
  <arguments>-m python_mt5_sidecar http-server --config &quot;$(& $escape (Join-Path $root '.deploy\bootstrap-http.yaml'))&quot;</arguments>
  <workingdirectory>$(& $escape $root)</workingdirectory>
  <startmode>Manual</startmode>
</service>
"@
[IO.File]::WriteAllText((Join-Path $directory 'python-mt5-http.xml'), $xml, (New-Object Text.UTF8Encoding($false)))
& $wrapper install
if ($LASTEXITCODE -ne 0) { throw 'Business service registration failed.' }
Write-Output 'Business service registered without starting. Set its logon identity to the MT5 credential owner; the first Agent release supplies the runtime.'

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$sourcePath = Join-Path $PSScriptRoot '..\..\windows-agent\Invoke-Mt5Release.ps1'
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw ('Release adapter syntax errors: ' + ($parseErrors.Message -join '; ')) }
$functions = @($ast.FindAll({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -in @('Test-StreamingHealth', 'Get-StreamingHealth')
}, $true))
if ($functions.Count -ne 2) { throw 'Streaming health functions were not found.' }
foreach ($function in $functions) { . ([ScriptBlock]::Create($function.Extent.Text)) }
$cases = @(
    @{code=200; json='{"enabled":true,"status":"LIVE"}'; expected=$true},
    @{code=503; json='{"enabled":true,"status":"DOWN","accountState":{"enabled":true,"status":"STANDBY"},"groupPrice":{"enabled":true,"status":"STANDBY"}}'; expected=$true},
    @{code=503; json='{"enabled":true,"status":"DOWN","accountState":{"enabled":true,"status":"LIVE"},"groupPrice":{"enabled":true,"status":"STANDBY"}}'; expected=$true},
    @{code=503; json='{"enabled":true,"status":"DOWN","accountState":{"enabled":true,"status":"STANDBY"},"groupPrice":{"enabled":false,"status":"DISABLED"}}'; expected=$true},
    @{code=503; json='{"enabled":true,"status":"DOWN","accountState":{"enabled":true,"status":"STANDBY"}}'; expected=$false},
    @{code=503; json='{"enabled":true,"status":"DOWN","accountState":{"enabled":true,"status":"ERROR"},"groupPrice":{"enabled":true,"status":"STANDBY"}}'; expected=$false},
    @{code=503; json='{"enabled":true,"status":"DOWN","accountState":{"enabled":true,"status":"STARTING"},"groupPrice":{"enabled":true,"status":"STANDBY"}}'; expected=$false},
    @{code=503; json='{"enabled":false,"status":"DOWN","accountState":{"enabled":true,"status":"STANDBY"}}'; expected=$false},
    @{code=503; json='{"enabled":true,"status":"DOWN","accountState":{"enabled":false,"status":"DISABLED"}}'; expected=$false},
    @{code=503; json='{"enabled":true,"status":"DOWN"}'; expected=$false},
    @{code=500; json='{"enabled":true,"status":"DOWN","accountState":{"enabled":true,"status":"STANDBY"}}'; expected=$false}
)
foreach ($case in $cases) {
    $actual = Test-StreamingHealth ($case.json | ConvertFrom-Json) $case.code
    if ($actual -ne $case.expected) { throw "Unexpected streaming health result for HTTP $($case.code): $($case.json)" }
}
Write-Host 'PASS: streaming LIVE and standby acceptance; unhealthy statuses rejected.'

if (-not ('DeployAgentTestResponse' -as [type])) {
    Add-Type -TypeDefinition @'
using System.IO;
using System.Net;
using System.Text;
public class DeployAgentTestResponse : WebResponse {
    public HttpStatusCode StatusCode { get; set; }
    public string Body { get; set; }
    public int StreamReads { get; private set; }
    public bool Closed { get; private set; }
    public override Stream GetResponseStream() {
        StreamReads++;
        return new MemoryStream(Encoding.UTF8.GetBytes(Body));
    }
    public override void Close() { Closed = true; }
}
'@
}
# Exercise the real HTTP helper with the same ErrorRecord shape as PowerShell 5.1.
# The fake request never contacts a server or changes a service.
function Invoke-RestMethod {
    [CmdletBinding()]
    param([string]$Uri, [int]$TimeoutSec)
    if ($script:testResponse.StatusCode -eq [Net.HttpStatusCode]::OK) {
        return $script:testResponse.Body | ConvertFrom-Json
    }
    $exception = [Net.WebException]::new('Test HTTP failure', $null, [Net.WebExceptionStatus]::ProtocolError, $script:testResponse)
    $record = [Management.Automation.ErrorRecord]::new($exception, 'TestHttpFailure', [Management.Automation.ErrorCategory]::InvalidOperation, $null)
    if ($null -ne $script:testErrorDetails) {
        $record.ErrorDetails = [Management.Automation.ErrorDetails]::new($script:testErrorDetails)
    }
    $PSCmdlet.ThrowTerminatingError($record)
}
$standbyJson = $cases[1].json
$httpCases = @(
    @{name='consumed stream'; code=503; details=$standbyJson; body=''; reads=0; accepted=$true},
    @{name='stream fallback'; code=503; details=$null; body=$standbyJson; reads=1; accepted=$true},
    @{name='blank details fallback'; code=503; details=' '; body=$standbyJson; reads=1; accepted=$true},
    @{name='unhealthy response'; code=503; details=$cases[5].json; body=''; reads=0; accepted=$false},
    @{name='empty response'; code=503; details=$null; body=''; reads=1; accepted=$false},
    @{name='invalid JSON'; code=503; details='<html>Unavailable</html>'; body=''; reads=0; accepted=$false},
    @{name='other HTTP failure'; code=500; details=$standbyJson; body=''; reads=0; accepted=$false},
    @{name='live response'; code=200; details=$null; body=$cases[0].json; reads=0; accepted=$true}
)
try {
    foreach ($case in $httpCases) {
        $script:testResponse = [DeployAgentTestResponse]::new()
        $script:testResponse.StatusCode = [Net.HttpStatusCode]$case.code
        $script:testResponse.Body = $case.body
        $script:testErrorDetails = $case.details
        $accepted = $false
        try {
            $result = Get-StreamingHealth 'http://test.invalid/health/streaming'
            $accepted = Test-StreamingHealth $result.body $result.statusCode
        } catch {
            if ($case.accepted) { throw }
        }
        if ($accepted -ne $case.accepted) { throw ('Unexpected HTTP health result: ' + $case.name) }
        if ($script:testResponse.StreamReads -ne $case.reads) { throw ('Unexpected response stream access: ' + $case.name) }
        if ($case.code -eq 503 -and -not $script:testResponse.Closed) { throw ('Response was not closed: ' + $case.name) }
        Write-Host ('PASS: ' + $case.name)
    }
} finally { Remove-Item Function:\Invoke-RestMethod }

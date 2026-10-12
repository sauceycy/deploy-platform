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

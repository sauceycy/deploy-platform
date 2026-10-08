$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$sourcePath = Join-Path $PSScriptRoot '..\..\windows-agent\Invoke-Mt5Release.ps1'
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($sourcePath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw ('Release adapter syntax errors: ' + ($parseErrors.Message -join '; ')) }
$functions = @($ast.FindAll({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -in @('Write-EnvironmentFile', 'Apply-ConfiguredEnvironment', 'Restore-EnvironmentFile')
}, $true))
if ($functions.Count -ne 3) { throw 'Environment transaction functions were not found.' }
foreach ($function in $functions) { . ([ScriptBlock]::Create($function.Extent.Text)) }
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('agent-env-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$envPath = Join-Path $testRoot '.env'
$envSupplied = $true
$config = [PSCustomObject]@{EnvContent="APP_ENV=prod`nNACOS_PASSWORD=test-only"}
try {
    foreach ($scenario in @('missing', 'empty', 'existing')) {
        if (Test-Path -LiteralPath $envPath) { Remove-Item -LiteralPath $envPath }
        $envExisted = $scenario -ne 'missing'
        $oldEnv = $null
        $envApplied = $false
        if ($envExisted) {
            $priorText = if ($scenario -eq 'empty') { '' } else { "APP_ENV=test`r`nNACOS_PASSWORD=old-test-only" }
            [IO.File]::WriteAllText($envPath, $priorText, (New-Object Text.UTF8Encoding($false)))
            $oldEnv = [IO.File]::ReadAllBytes($envPath)
        }
        Apply-ConfiguredEnvironment
        if ([IO.File]::ReadAllText($envPath) -ne $config.EnvContent) { throw ('New .env content differs: ' + $scenario) }
        if (-not $envApplied) { throw 'Environment application was not recorded.' }
        # The first release creates .env during preparation and replaces it at cutover.
        # Exercise the second write even when this scenario began without a file.
        Apply-ConfiguredEnvironment
        if ([IO.File]::ReadAllText($envPath) -ne $config.EnvContent) { throw ('Repeated replacement differs: ' + $scenario) }
        Restore-EnvironmentFile
        if ($envExisted) {
            if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($envPath)) -ne [Convert]::ToBase64String($oldEnv)) {
                throw ('Original bytes were not restored: ' + $scenario)
            }
        } elseif (Test-Path -LiteralPath $envPath) { throw 'Failed first deployment left a new .env behind.' }
        if ($envApplied) { throw 'Environment restoration state was not reset.' }
        Restore-EnvironmentFile
        if (@(Get-ChildItem -LiteralPath $testRoot -Filter '*.tmp').Count -ne 0) { throw 'Temporary environment files remain.' }
        if (@(Get-ChildItem -LiteralPath $testRoot -Filter '*.bak').Count -ne 0) { throw 'Temporary environment backups remain.' }
        Write-Host ('PASS: ' + $scenario)
    }
    $envSupplied = $false
    $priorBytes = [IO.File]::ReadAllBytes($envPath)
    Apply-ConfiguredEnvironment
    if ($envApplied) { throw 'Blank deployment config should not replace the existing file.' }
    if ([Convert]::ToBase64String([IO.File]::ReadAllBytes($envPath)) -ne [Convert]::ToBase64String($priorBytes)) {
        throw 'No supplied environment changed the existing file.'
    }
    Write-Host 'PASS: no supplied environment'
} finally { Remove-Item -LiteralPath $testRoot -Recurse -Force }

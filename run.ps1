$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

if ([string]::IsNullOrWhiteSpace($env:CLINIC_QUEUE_CONFIG)) {
    $configPath = Join-Path $projectRoot 'config.json'
} else {
    $configPath = $env:CLINIC_QUEUE_CONFIG
    if (-not [System.IO.Path]::IsPathRooted($configPath)) {
        $configPath = Join-Path $projectRoot $configPath
    }
}

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    Write-Output "Configuration file not found: $configPath"
    Write-Output 'Copy config.example.json to config.json, then edit the HIS path and other local settings.'
    exit 2
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) {
    $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -eq $pythonLauncher) {
        Write-Output 'Python 3 is required. Install Python 3 and add python or py to PATH.'
        exit 2
    }
    $pythonCommand = $pythonLauncher.Source
    $pythonArguments = @('-3', '-m', 'clinic_queue')
} else {
    $pythonCommand = $python.Source
    $pythonArguments = @('-m', 'clinic_queue')
}

$env:CLINIC_QUEUE_CONFIG = (Resolve-Path -LiteralPath $configPath).Path
$previousLocation = Get-Location
Set-Location -LiteralPath $projectRoot
try {
    & $pythonCommand @pythonArguments
    $applicationExitCode = $LASTEXITCODE
} finally {
    Set-Location -LiteralPath $previousLocation
}
exit $applicationExitCode

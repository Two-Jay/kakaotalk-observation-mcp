param([string]$DestinationRoot = (Join-Path $HOME 'project_personal'))
$ErrorActionPreference = 'Stop'
$sourceDirectory = $PSScriptRoot
$targetDirectory = Join-Path $DestinationRoot 'kakao-mention-watch'
if (Test-Path -LiteralPath $targetDirectory) {
    throw "Target already exists; nothing was overwritten: $targetDirectory"
}
New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
New-Item -ItemType Directory -Path $targetDirectory | Out-Null
Get-ChildItem -LiteralPath $sourceDirectory -Force |
    Where-Object { $_.Name -notin @('.venv', '__pycache__', 'data', 'config.json') } |
    Copy-Item -Destination $targetDirectory -Recurse
Write-Output "Copied project to $targetDirectory"

#requires -Version 7
param([string]$Config = (Join-Path $PSScriptRoot 'remote.local.json'))
$ErrorActionPreference = 'Stop'
$remotePython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$remoteSource = Join-Path $PSScriptRoot 'kakao-mention-watch\kakao-mention-watch'
$remoteData = Join-Path $PSScriptRoot 'data\demo'
$remoteOutput = Join-Path $PSScriptRoot 'data\remote'
if (-not (Test-Path -LiteralPath $Config)) { throw 'remote.example.json을 remote.local.json으로 복사하고 설정하세요.' }
& $remotePython (Join-Path $remoteSource 'remote_control.py') validate --config $Config
if ($LASTEXITCODE -ne 0) { throw '원격 설정이 완료되지 않았거나 공유가 꺼져 있습니다.' }
New-Item -ItemType Directory -Path $remoteOutput -Force | Out-Null
$remoteSnapshot = Join-Path $remoteOutput 'summary.json'
# Start-Process handles all paths as quoted arguments; no token or password is passed.
$publisherArguments = @(
    ('"' + (Join-Path $remoteSource 'share_snapshot.py') + '"'),
    '--config', ('"' + (Resolve-Path -LiteralPath $Config).Path + '"'),
    '--data', ('"' + $remoteData + '"'), '--output', ('"' + $remoteSnapshot + '"'), '--watch'
)
$publisherProcess = Start-Process -FilePath $remotePython -ArgumentList $publisherArguments -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $remoteOutput 'publisher.stdout.log') `
    -RedirectStandardError (Join-Path $remoteOutput 'publisher.stderr.log')
try {
    & $remotePython (Join-Path $remoteSource 'remote_server.py') --config $Config --snapshot $remoteSnapshot --audit-log (Join-Path $remoteOutput 'access.log')
} finally {
    if (-not $publisherProcess.HasExited) { $publisherProcess.Kill($true); $publisherProcess.WaitForExit() }
}

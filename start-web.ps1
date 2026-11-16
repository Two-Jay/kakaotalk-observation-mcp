param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw '프로젝트 .venv가 없습니다. README의 설치 명령을 먼저 실행하세요.'
}
& $pythonPath (Join-Path $PSScriptRoot 'kakao-mention-watch\kakao-mention-watch\web_server.py') --port $Port --data (Join-Path $PSScriptRoot 'data\demo')

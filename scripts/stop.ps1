$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& (Join-Path $projectRoot '.venv\Scripts\python.exe') -m api_post stop
if ($LASTEXITCODE -ne 0) { throw '无法停止本地服务，请检查工作区与服务状态。' }

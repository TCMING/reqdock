param([switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw '请先按 README 安装本地依赖。' }
$runtime = Join-Path $projectRoot 'workspace\runtime'
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$running = $false
try {
    $response = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/health' -TimeoutSec 2
    if ($response.app -ne 'api-post-local') { throw '端口由其他应用占用' }
    & $pythonExe -m api_post status | Out-Null
    if ($LASTEXITCODE -ne 0) { throw '本地实例身份核对失败' }
    $running = $true
} catch {
    if (Get-NetTCPConnection -LocalPort 18765 -State Listen -ErrorAction SilentlyContinue) {
        throw '端口已占用，但无法确认工作区实例身份。'
    }
}
if (-not $running) {
    $outLog = Join-Path $runtime 'service.stdout.log'
    $errLog = Join-Path $runtime 'service.stderr.log'
    Start-Process -FilePath $pythonExe -ArgumentList @('-m','api_post','serve') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog | Out-Null
    $ready = $false
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 300
        try {
            $response = Invoke-RestMethod -Uri 'http://127.0.0.1:18765/api/health' -TimeoutSec 1
            if ($response.app -eq 'api-post-local') { $ready=$true; break }
        } catch {}
    }
    if (-not $ready) { throw "启动失败，请检查 $errLog" }
}
Write-Output 'ReqDock 已启动：http://127.0.0.1:18765'
if ($OpenBrowser) { & $pythonExe -m api_post open }

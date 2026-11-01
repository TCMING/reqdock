$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The installed .NET Framework compiler is required.' }
$icon=Join-Path $projectRoot 'assets\api-lab.ico'
if (-not (Test-Path -LiteralPath $icon)) { throw 'Application icon missing.' }
$output=Join-Path $projectRoot 'ReqDock.exe'
& $compiler /nologo /target:winexe "/win32icon:$icon" /reference:System.Windows.Forms.dll "/out:$output" (Join-Path $PSScriptRoot 'ClientLauncher.cs')
if ($LASTEXITCODE -ne 0) { throw 'Client launcher build failed.' }
Write-Output $output

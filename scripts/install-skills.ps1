param(
    [ValidateSet('Both','Codex','ClaudeCode')][string]$Client='Both',
    [string]$Workspace=''
)
$ErrorActionPreference='Stop'
$projectRoot=[IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$package=Join-Path $projectRoot 'skills\reqdock'
$python=Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath (Join-Path $package 'SKILL.md'))) { throw 'Install the ReqDock runtime before installing its Skills.' }
$profileRoot=[Environment]::GetFolderPath('UserProfile')
$localRoot=Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ReqDock'
$runtimePath=Join-Path $localRoot 'runtime.json'
$stamp=(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8)
$backupRoot=Join-Path $localRoot ('skill-backups\'+$stamp)
$utf8=New-Object Text.UTF8Encoding($false)
if ([string]::IsNullOrWhiteSpace($Workspace)) {
    $Workspace=Join-Path $projectRoot 'workspace'
    if (Test-Path -LiteralPath $runtimePath) {
        $existing=Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
        if ($existing.project_root -eq $projectRoot) { $Workspace=$existing.workspace }
    }
}
if (-not [IO.Path]::IsPathRooted($Workspace)) { throw 'Workspace must be an absolute path.' }
$Workspace=[IO.Path]::GetFullPath($Workspace)
$targets=@()
if ($Client -in @('Both','Codex')) { $targets+=@{name='Codex';path=(Join-Path $profileRoot '.agents\skills\reqdock')} }
if ($Client -in @('Both','ClaudeCode')) { $targets+=@{name='ClaudeCode';path=(Join-Path $profileRoot '.claude\skills\reqdock')} }
foreach ($entry in $targets) {
    $probe=$entry.path
    while ($probe -and $probe -ne $profileRoot) {
        if ((Test-Path -LiteralPath $probe) -and ((Get-Item -LiteralPath $probe -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'A Skill target is a junction or symbolic link; no changes made.' }
        $probe=Split-Path -Parent $probe
    }
}
New-Item -ItemType Directory -Path $localRoot -Force | Out-Null
if (Test-Path -LiteralPath $runtimePath) {
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    Copy-Item -LiteralPath $runtimePath -Destination (Join-Path $backupRoot 'runtime.json')
}
foreach ($entry in $targets) {
    if (Test-Path -LiteralPath $entry.path) {
        New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
        Copy-Item -LiteralPath $entry.path -Destination (Join-Path $backupRoot $entry.name) -Recurse
    }
    New-Item -ItemType Directory -Path $entry.path -Force | Out-Null
    foreach ($file in Get-ChildItem -LiteralPath $package -Recurse -File) {
        if ($file.Name -eq 'config.json' -or $file.FullName -match '[\\/]__pycache__[\\/]') { continue }
        $relative=$file.FullName.Substring($package.Length).TrimStart([char[]]'\/')
        $destination=Join-Path $entry.path $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }
    $config=@{project_root=$projectRoot;agent_name=$entry.name}|ConvertTo-Json
    [IO.File]::WriteAllText((Join-Path $entry.path 'config.json'),$config,$utf8)
    Write-Output ('Installed '+$entry.name+' Skill: '+$entry.path)
}
$runtime=@{schema_version=1;project_root=$projectRoot;workspace=$Workspace}|ConvertTo-Json
$tempPath=Join-Path $localRoot ('runtime.'+[guid]::NewGuid().ToString('N')+'.tmp')
[IO.File]::WriteAllText($tempPath,$runtime,$utf8)
Move-Item -LiteralPath $tempPath -Destination $runtimePath -Force
Write-Output ('Workspace kept in place: '+$Workspace)
Write-Output 'Existing API Lab Skills and workspace files were not moved or deleted.'
if (Test-Path -LiteralPath $backupRoot) { Write-Output ('Previous integration configuration backed up: '+$backupRoot) }

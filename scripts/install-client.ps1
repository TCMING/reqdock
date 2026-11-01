$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$task=Get-ScheduledTask -TaskName 'ReqDock - Local Workbench' -ErrorAction SilentlyContinue
if ($task) { Disable-ScheduledTask -TaskName $task.TaskName | Out-Null }
$shell=New-Object -ComObject WScript.Shell
$desktop=[Environment]::GetFolderPath('Desktop')
$launcher=Join-Path $projectRoot 'ReqDock.exe'
if (-not (Test-Path -LiteralPath $launcher)) { & (Join-Path $PSScriptRoot 'build-client.ps1') | Out-Null }
$shortcutFolders=@($desktop,(Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'))
foreach($folder in $shortcutFolders) {
    if (-not (Test-Path -LiteralPath $folder)) { New-Item -ItemType Directory -Path $folder | Out-Null }
    $shortcut=$shell.CreateShortcut((Join-Path $folder 'ReqDock.lnk'))
    $shortcut.TargetPath=$launcher
    $shortcut.IconLocation=(Join-Path $projectRoot 'assets\api-lab.ico') + ',0'
    $shortcut.Arguments=''
    $shortcut.WorkingDirectory=$projectRoot
    $shortcut.Description='On-demand ReqDock. Closing the client stops its local service.'
    $shortcut.Save()
}
Write-Output 'Desktop and Start menu shortcuts created; logon autostart disabled.'

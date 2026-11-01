param([ValidateSet('Enable','Disable','Status','Start')][string]$Action='Status')
$ErrorActionPreference = 'Stop'
$taskName = 'ReqDock - Local Workbench'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonwExe = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if ($Action -eq 'Enable') {
    if (-not (Test-Path -LiteralPath $pythonwExe)) { throw 'Missing project virtual environment.' }
    $account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing -and ($existing.Actions.Execute -ne $pythonwExe)) { throw 'Task name is owned by a different application.' }
    $execute = New-ScheduledTaskAction -Execute $pythonwExe -Argument '-m api_post serve' -WorkingDirectory $projectRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $account
    $trigger.Delay = 'PT10S'
    $principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $task = New-ScheduledTask -Action $execute -Trigger $trigger -Principal $principal -Settings $settings -Description 'Local API workbench. Starts after this user signs in; loopback only, single worker, no browser window.'
    Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
} elseif ($Action -eq 'Disable') {
    Disable-ScheduledTask -TaskName $taskName | Out-Null
} elseif ($Action -eq 'Start') {
    Start-ScheduledTask -TaskName $taskName
}
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State,@{N='Execute';E={$_.Actions.Execute}},@{N='LogonType';E={$_.Principal.LogonType}},@{N='ExecutionTimeLimit';E={$_.Settings.ExecutionTimeLimit}}

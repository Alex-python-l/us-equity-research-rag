param([ValidateSet('Install','Status','Start','Remove')][string]$Action='Status')
$ErrorActionPreference='Stop'
$ProjectRoot=Split-Path -Parent $PSScriptRoot
$TaskName='US-Equity-Research-RAG'
if($Action -eq 'Install') {
    $PythonPath=Join-Path $ProjectRoot 'tech_rag\integration\.venv\Scripts\pythonw.exe'
    $RunnerPath=Join-Path $PSScriptRoot 'run_background.py'
    if(!(Test-Path -LiteralPath $PythonPath)){throw 'Existing Python runtime missing'}
    $TaskAction=New-ScheduledTaskAction -Execute $PythonPath -Argument ('"'+$RunnerPath+'"') -WorkingDirectory $ProjectRoot
    $Trigger=New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
    $Principal=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    $Settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $TaskAction -Trigger $Trigger -Principal $Principal -Settings $Settings -Description 'Local research backend; only explicitly confirmed monitoring plans execute.' -Force | Out-Null
} elseif($Action -eq 'Start') {
    Start-ScheduledTask -TaskName $TaskName
} elseif($Action -eq 'Remove') {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object LastRunTime,LastTaskResult,NextRunTime

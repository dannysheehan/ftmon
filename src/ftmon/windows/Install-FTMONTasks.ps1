#Requires -Version 5.1
<#
.SYNOPSIS
  Register or remove FTMON Task Scheduler startup tasks (PL-01 / DO-02).

.DESCRIPTION
  Install always creates or updates "FTMON daemon". -IncludeWeb additionally
  creates or updates "FTMON web". Installing without -IncludeWeb does not
  delete an existing web task. -Action Remove idempotently stops and
  unregisters both official tasks. Registration never silently starts or
  restarts a process — start tasks explicitly after install.

.PARAMETER Action
  Install (default) or Remove.

.PARAMETER FtmonExe
  Absolute path to ftmon.exe. When omitted, resolved via Get-Command ftmon.

.PARAMETER IncludeWeb
  Also register the optional persistent web task (loopback-only; NG-05).
#>
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Remove')]
    [string]$Action = 'Install',

    [string]$FtmonExe,

    [switch]$IncludeWeb
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:DaemonTaskName = 'FTMON daemon'
$script:WebTaskName = 'FTMON web'
$script:OfficialTaskNames = @($script:DaemonTaskName, $script:WebTaskName)

function Write-FtmonInfo {
    param([string]$Message)
    Write-Host "ftmon-tasks: $Message"
}

function Assert-NotLocalSystem {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($identity.IsSystem) {
        throw 'Refuse to register FTMON tasks as LocalSystem. Run as the monitoring account.'
    }
    # SID S-1-5-18 is Local System even when IsSystem is unexpected.
    if ($identity.User -and $identity.User.Value -eq 'S-1-5-18') {
        throw 'Refuse to register FTMON tasks as LocalSystem. Run as the monitoring account.'
    }
}

function Resolve-FtmonExecutable {
    param([string]$Explicit)
    if (-not [string]::IsNullOrWhiteSpace($Explicit)) {
        if (-not [System.IO.Path]::IsPathRooted($Explicit)) {
            throw "-FtmonExe must be an absolute path: $Explicit"
        }
        $path = [System.IO.Path]::GetFullPath($Explicit)
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "ftmon executable not found: $path"
        }
        return $path
    }

    $cmd = Get-Command -Name ftmon -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $cmd) {
        $cmd = Get-Command -Name ftmon.exe -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
    }
    if (-not $cmd) {
        throw 'ftmon.exe not found on PATH. Pass -FtmonExe with an absolute path, or install FTMON first.'
    }
    $path = [System.IO.Path]::GetFullPath($cmd.Source)
    if (-not [System.IO.Path]::IsPathRooted($path)) {
        throw "Resolved ftmon path is not absolute: $path"
    }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Resolved ftmon executable does not exist: $path"
    }
    return $path
}

function Get-FtmonPathsJson {
    param([string]$Exe)
    $raw = & $Exe paths --json 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "ftmon paths --json failed (exit $LASTEXITCODE): $raw"
    }
    $text = ($raw | Out-String).Trim()
    try {
        return $text | ConvertFrom-Json
    }
    catch {
        throw "ftmon paths --json did not return JSON: $text"
    }
}

function Assert-Initialized {
    param($Paths)
    if (-not $Paths.config_file) {
        throw 'ftmon paths --json did not include config_file.'
    }
    if (-not (Test-Path -LiteralPath $Paths.config_file -PathType Leaf)) {
        throw @"
config.toml is missing at $($Paths.config_file).
Run ``ftmon init --profile desktop`` (or windesktop/winserver) before installing startup tasks.
"@
    }
    if (-not $Paths.state_dir) {
        throw 'ftmon paths --json did not include state_dir.'
    }
}

function Get-WindowsPowerShellPath {
    $systemRoot = $env:SystemRoot
    if ([string]::IsNullOrWhiteSpace($systemRoot)) {
        $systemRoot = 'C:\Windows'
    }
    $path = Join-Path $systemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Windows PowerShell not found at $path"
    }
    return $path
}

function Get-BundledRunnerPath {
    # Prefer a sibling of this installer (uv Scripts/ or MSI install dir),
    # then the package-data copy under the installed ftmon package.
    $sibling = Join-Path $PSScriptRoot 'Invoke-FTMONTask.ps1'
    if (Test-Path -LiteralPath $sibling -PathType Leaf) {
        return $sibling
    }
    throw "Invoke-FTMONTask.ps1 not found beside Install-FTMONTasks.ps1 ($PSScriptRoot)."
}

function Install-RunnerCopy {
    param(
        [string]$StateDir,
        [string]$SourceRunner
    )
    $tasksDir = Join-Path $StateDir 'tasks'
    if (-not (Test-Path -LiteralPath $tasksDir -PathType Container)) {
        New-Item -ItemType Directory -Path $tasksDir -Force | Out-Null
    }
    $dest = Join-Path $tasksDir 'Invoke-FTMONTask.ps1'
    Copy-Item -LiteralPath $SourceRunner -Destination $dest -Force
    return @{
        TasksDir = $tasksDir
        Runner   = $dest
    }
}

function New-FtmonTaskAction {
    param(
        [string]$PowerShellExe,
        [string]$RunnerPath,
        [string]$Role,
        [string]$FtmonExe,
        [string]$LogFile,
        [string]$WorkingDirectory
    )
    # Hidden applies to the console window, not Task Scheduler discoverability.
    $argList = @(
        '-NoProfile'
        '-NonInteractive'
        '-WindowStyle', 'Hidden'
        '-ExecutionPolicy', 'RemoteSigned'
        '-File', "`"$RunnerPath`""
        '-Role', $Role
        '-FtmonExe', "`"$FtmonExe`""
        '-LogFile', "`"$LogFile`""
    ) -join ' '
    return New-ScheduledTaskAction `
        -Execute $PowerShellExe `
        -Argument $argList `
        -WorkingDirectory $WorkingDirectory
}

function New-FtmonTaskSettings {
    # Restart every minute, max 255; IgnoreNew; indefinite; battery OK;
    # StartWhenAvailable. Do not set Hidden on the task itself.
    # Battery policy maps to DisallowStartIfOnBatteries / StopIfGoingOnBatteries
    # (the CIM properties); -AllowStartIfOnBatteries is the cmdlet switch that
    # clears the disallow flag. -DontStopOnIdleEnd clears the default idle stop
    # that otherwise terminates long-running interactive tasks.
    return New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -DontStopOnIdleEnd `
        -StartWhenAvailable `
        -RestartCount 255 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew
}

function New-FtmonLogonTrigger {
    $userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    return New-ScheduledTaskTrigger -AtLogOn -User $userId
}

function New-FtmonPrincipal {
    $userId = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    return New-ScheduledTaskPrincipal `
        -UserId $userId `
        -LogonType Interactive `
        -RunLevel Limited
}

function Register-FtmonTask {
    param(
        [string]$TaskName,
        [string]$Role,
        [string]$PowerShellExe,
        [string]$RunnerPath,
        [string]$FtmonExe,
        [string]$StateDir,
        [string]$WorkingDirectory
    )
    $logFile = Join-Path $StateDir "task-$Role.log"
    $action = New-FtmonTaskAction `
        -PowerShellExe $PowerShellExe `
        -RunnerPath $RunnerPath `
        -Role $Role `
        -FtmonExe $FtmonExe `
        -LogFile $logFile `
        -WorkingDirectory $WorkingDirectory
    $settings = New-FtmonTaskSettings
    $trigger = New-FtmonLogonTrigger
    $principal = New-FtmonPrincipal
    $description = "FTMON $Role (per-user, limited, logon). Managed by Install-FTMONTasks.ps1."

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description $description `
        -Force | Out-Null

    Write-FtmonInfo "registered '$TaskName' (not started)."
}

function Remove-FtmonTaskIfPresent {
    param([string]$TaskName)
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-FtmonInfo "'$TaskName' not present."
        return
    }
    if ($existing.State -eq 'Running') {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-FtmonInfo "removed '$TaskName'."
}

Assert-NotLocalSystem

if ($Action -eq 'Remove') {
    foreach ($name in $script:OfficialTaskNames) {
        Remove-FtmonTaskIfPresent -TaskName $name
    }
    Write-FtmonInfo 'removal complete.'
    exit 0
}

$exe = Resolve-FtmonExecutable -Explicit $FtmonExe
$paths = Get-FtmonPathsJson -Exe $exe
Assert-Initialized -Paths $paths

$sourceRunner = Get-BundledRunnerPath
$installed = Install-RunnerCopy -StateDir $paths.state_dir -SourceRunner $sourceRunner
$psExe = Get-WindowsPowerShellPath

Register-FtmonTask `
    -TaskName $script:DaemonTaskName `
    -Role 'daemon' `
    -PowerShellExe $psExe `
    -RunnerPath $installed.Runner `
    -FtmonExe $exe `
    -StateDir $paths.state_dir `
    -WorkingDirectory $installed.TasksDir

if ($IncludeWeb) {
    Register-FtmonTask `
        -TaskName $script:WebTaskName `
        -Role 'web' `
        -PowerShellExe $psExe `
        -RunnerPath $installed.Runner `
        -FtmonExe $exe `
        -StateDir $paths.state_dir `
        -WorkingDirectory $installed.TasksDir
}
else {
    Write-FtmonInfo "daemon-only install; existing '$($script:WebTaskName)' (if any) left unchanged."
}

Write-FtmonInfo @"
install complete. Tasks are registered but not started.
Start with:  Start-ScheduledTask -TaskName '$($script:DaemonTaskName)'
Status:      Get-ScheduledTask -TaskName 'FTMON*'; ftmon status; ftmon doctor
"@

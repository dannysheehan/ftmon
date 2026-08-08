#Requires -Version 5.1
<#
.SYNOPSIS
  Internal Task Scheduler runner for FTMON daemon or web (PL-01).

.DESCRIPTION
  Accepts only "daemon" or "web". Invokes the absolute FTMON executable
  synchronously. Wrapper stdout/stderr go to a per-role task log under the
  FTMON state directory; logs larger than 1 MiB roll to a single .1 backup
  before each start. The daemon's own rotating log remains authoritative.

  On non-zero child exit (including forced kill), the wrapper waits 60 seconds
  and retries up to 255 times — matching the scheduled-task RestartInterval /
  RestartCount contract. InteractiveToken + Unified Scheduling Engine on
  current Windows hosts often does not honor RestartOnFailure for demand or
  logon-started tasks; the wrapper loop is the reliable recovery path while
  the task settings remain declared for hosts that do honor them.

  Copied to <state_dir>\tasks\ by Install-FTMONTasks.ps1 so execution never
  depends on a checkout or temporary package location.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('daemon', 'web')]
    [string]$Role,

    [Parameter(Mandatory = $true)]
    [string]$FtmonExe,

    [Parameter(Mandatory = $true)]
    [string]$LogFile
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-AbsoluteExistingFile {
    param([string]$PathValue, [string]$Label)
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        throw "$Label is required."
    }
    if (-not [System.IO.Path]::IsPathRooted($PathValue)) {
        throw "$Label must be an absolute path: $PathValue"
    }
    $resolved = [System.IO.Path]::GetFullPath($PathValue)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "$Label does not exist: $resolved"
    }
    return $resolved
}

function Rotate-TaskLogIfNeeded {
    param([string]$Path)
    $maxBytes = 1MB
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    $item = Get-Item -LiteralPath $Path
    if ($item.Length -le $maxBytes) {
        return
    }
    $backup = "$Path.1"
    if (Test-Path -LiteralPath $backup) {
        Remove-Item -LiteralPath $backup -Force
    }
    Move-Item -LiteralPath $Path -Destination $backup -Force
}

function Invoke-FtmonOnce {
    param(
        [string]$Exe,
        [string]$RoleName,
        [string]$LogPath
    )
    # Redirect both streams to temp files so neither pipe can fill and deadlock
    # the parent (sequential ReadToEnd of stdout-then-stderr hung on a 2 MB
    # stderr probe). Append into the task log after the child exits.
    $tempOut = Join-Path $env:TEMP ("ftmon-task-out-" + [guid]::NewGuid().ToString('N') + ".log")
    $tempErr = Join-Path $env:TEMP ("ftmon-task-err-" + [guid]::NewGuid().ToString('N') + ".log")
    try {
        $proc = Start-Process `
            -FilePath $Exe `
            -ArgumentList $RoleName `
            -WorkingDirectory (Get-Location).Path `
            -WindowStyle Hidden `
            -Wait `
            -PassThru `
            -RedirectStandardOutput $tempOut `
            -RedirectStandardError $tempErr

        foreach ($capture in @($tempOut, $tempErr)) {
            if ((Test-Path -LiteralPath $capture) -and (Get-Item -LiteralPath $capture).Length -gt 0) {
                Get-Content -LiteralPath $capture -Encoding utf8 | ForEach-Object {
                    Add-Content -LiteralPath $LogPath -Encoding utf8 -Value $_
                }
            }
        }

        $code = 1
        if ($null -ne $proc.ExitCode) {
            $code = [int]$proc.ExitCode
        }
        if ($code -lt 0 -or $code -gt 255) {
            $code = 1
        }
        return $code
    }
    finally {
        Remove-Item -LiteralPath $tempOut, $tempErr -Force -ErrorAction SilentlyContinue
    }
}

$ftmon = Assert-AbsoluteExistingFile -PathValue $FtmonExe -Label 'FtmonExe'
$logPath = [System.IO.Path]::GetFullPath($LogFile)
$logDir = Split-Path -Parent $logPath
if (-not (Test-Path -LiteralPath $logDir -PathType Container)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

Rotate-TaskLogIfNeeded -Path $logPath

$maxAttempts = 255
if ($env:FTMON_TASK_MAX_ATTEMPTS -match '^\d+$') {
    $maxAttempts = [int]$env:FTMON_TASK_MAX_ATTEMPTS
}
$restartDelaySec = 60
if ($env:FTMON_TASK_RESTART_DELAY_SEC -match '^\d+$') {
    $restartDelaySec = [int]$env:FTMON_TASK_RESTART_DELAY_SEC
}
$attempt = 0
while ($attempt -lt $maxAttempts) {
    $attempt++
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ssK'
    Add-Content -LiteralPath $logPath -Encoding utf8 `
        -Value "[$stamp] starting $Role via $ftmon (attempt $attempt/$maxAttempts)"

    $code = Invoke-FtmonOnce -Exe $ftmon -RoleName $Role -LogPath $logPath

    $stampEnd = Get-Date -Format 'yyyy-MM-dd HH:mm:ssK'
    Add-Content -LiteralPath $logPath -Encoding utf8 `
        -Value "[$stampEnd] $Role exited $code"
    if ($code -eq 0) {
        exit 0
    }
    if ($attempt -ge $maxAttempts) {
        exit $code
    }
    Add-Content -LiteralPath $logPath -Encoding utf8 `
        -Value "[$stampEnd] restarting $Role in ${restartDelaySec}s"
    Start-Sleep -Seconds $restartDelaySec
}
exit 1

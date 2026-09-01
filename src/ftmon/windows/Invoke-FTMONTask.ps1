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

# Task Scheduler terminates this PowerShell wrapper when Stop-ScheduledTask is
# requested, but it does not automatically terminate a child created with
# Start-Process. Create FTMON with a STARTUPINFOEX job-list attribute so Windows
# associates it with a kill-on-close Job Object atomically. Descendants inherit
# membership; if Task Scheduler kills this wrapper while it waits, Windows
# closes the job handle and tears down the whole FTMON process tree.
if (-not ('FtmonTaskNativeJob' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

public static class FtmonTaskNativeJob
{
    private const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;
    private const int JobObjectExtendedLimitInformation = 9;
    private const uint CREATE_NO_WINDOW = 0x08000000;
    private const uint EXTENDED_STARTUPINFO_PRESENT = 0x00080000;
    private const uint GENERIC_READ = 0x80000000;
    private const uint GENERIC_WRITE = 0x40000000;
    private const uint FILE_SHARE_READ = 0x00000001;
    private const uint FILE_SHARE_WRITE = 0x00000002;
    private const uint CREATE_ALWAYS = 2;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_ATTRIBUTE_NORMAL = 0x00000080;
    private const uint STARTF_USESTDHANDLES = 0x00000100;
    private const uint INFINITE = 0xFFFFFFFF;
    private const uint STILL_ACTIVE = 259;
    private static readonly UIntPtr PROC_THREAD_ATTRIBUTE_HANDLE_LIST = new UIntPtr(0x00020002);
    private static readonly UIntPtr PROC_THREAD_ATTRIBUTE_JOB_LIST = new UIntPtr(0x0002000D);
    private static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SECURITY_ATTRIBUTES
    {
        public int nLength;
        public IntPtr lpSecurityDescriptor;
        public int bInheritHandle;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct STARTUPINFO
    {
        public int cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public int dwX;
        public int dwY;
        public int dwXSize;
        public int dwYSize;
        public int dwXCountChars;
        public int dwYCountChars;
        public int dwFillAttribute;
        public int dwFlags;
        public short wShowWindow;
        public short cbReserved2;
        public IntPtr lpReserved2;
        public IntPtr hStdInput;
        public IntPtr hStdOutput;
        public IntPtr hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct PROCESS_INFORMATION
    {
        public IntPtr hProcess;
        public IntPtr hThread;
        public uint dwProcessId;
        public uint dwThreadId;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct STARTUPINFOEX
    {
        public STARTUPINFO StartupInfo;
        public IntPtr lpAttributeList;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateJobObject(IntPtr attributes, string name);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool SetInformationJobObject(
        IntPtr job,
        int infoClass,
        IntPtr info,
        uint infoLength);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateProcess(
        string applicationName,
        StringBuilder commandLine,
        IntPtr processAttributes,
        IntPtr threadAttributes,
        bool inheritHandles,
        uint creationFlags,
        IntPtr environment,
        string currentDirectory,
        ref STARTUPINFOEX startupInfo,
        out PROCESS_INFORMATION processInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool InitializeProcThreadAttributeList(
        IntPtr attributeList,
        int attributeCount,
        int flags,
        ref IntPtr size);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool UpdateProcThreadAttribute(
        IntPtr attributeList,
        uint flags,
        UIntPtr attribute,
        IntPtr value,
        IntPtr size,
        IntPtr previousValue,
        IntPtr returnSize);

    [DllImport("kernel32.dll")]
    private static extern void DeleteProcThreadAttributeList(IntPtr attributeList);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateFile(
        string fileName,
        uint desiredAccess,
        uint shareMode,
        ref SECURITY_ATTRIBUTES securityAttributes,
        uint creationDisposition,
        uint flagsAndAttributes,
        IntPtr templateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool TerminateProcess(IntPtr process, uint exitCode);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(IntPtr handle);

    private static IntPtr CreateKillOnClose()
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateJobObject failed");

        try
        {
            SetKillOnClose(job);
            return job;
        }
        catch
        {
            CloseHandle(job);
            throw;
        }
    }

    private static void SetKillOnClose(IntPtr job)
    {
        var limits = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        int size = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
        IntPtr buffer = Marshal.AllocHGlobal(size);
        try
        {
            Marshal.StructureToPtr(limits, buffer, false);
            if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, buffer, (uint)size))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "SetInformationJobObject failed");
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }

    private static IntPtr OpenInheritedFile(string path, uint access, uint disposition)
    {
        var security = new SECURITY_ATTRIBUTES();
        security.nLength = Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES));
        security.bInheritHandle = 1;
        IntPtr handle = CreateFile(
            path,
            access,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            ref security,
            disposition,
            FILE_ATTRIBUTE_NORMAL,
            IntPtr.Zero);
        if (handle == INVALID_HANDLE_VALUE)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateFile failed: " + path);
        return handle;
    }

    public static int RunInKillOnCloseJob(
        string executable,
        string role,
        string workingDirectory,
        string stdoutPath,
        string stderrPath)
    {
        IntPtr job = IntPtr.Zero;
        IntPtr stdinHandle = IntPtr.Zero;
        IntPtr stdoutHandle = IntPtr.Zero;
        IntPtr stderrHandle = IntPtr.Zero;
        IntPtr attributeList = IntPtr.Zero;
        IntPtr jobList = IntPtr.Zero;
        IntPtr handleList = IntPtr.Zero;
        var processInfo = new PROCESS_INFORMATION();
        bool attributeListInitialized = false;
        bool created = false;
        try
        {
            job = CreateKillOnClose();
            stdinHandle = OpenInheritedFile("NUL", GENERIC_READ | GENERIC_WRITE, OPEN_EXISTING);
            stdoutHandle = OpenInheritedFile(stdoutPath, GENERIC_WRITE, CREATE_ALWAYS);
            stderrHandle = OpenInheritedFile(stderrPath, GENERIC_WRITE, CREATE_ALWAYS);

            IntPtr attributeBytes = IntPtr.Zero;
            InitializeProcThreadAttributeList(IntPtr.Zero, 2, 0, ref attributeBytes);
            if (attributeBytes == IntPtr.Zero)
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Attribute-list sizing failed");
            attributeList = Marshal.AllocHGlobal(attributeBytes);
            if (!InitializeProcThreadAttributeList(attributeList, 2, 0, ref attributeBytes))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "InitializeProcThreadAttributeList failed");
            attributeListInitialized = true;

            jobList = Marshal.AllocHGlobal(IntPtr.Size);
            Marshal.WriteIntPtr(jobList, job);
            if (!UpdateProcThreadAttribute(
                attributeList, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST,
                jobList, new IntPtr(IntPtr.Size), IntPtr.Zero, IntPtr.Zero))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Job-list attribute failed");

            handleList = Marshal.AllocHGlobal(IntPtr.Size * 3);
            Marshal.WriteIntPtr(handleList, 0, stdinHandle);
            Marshal.WriteIntPtr(handleList, IntPtr.Size, stdoutHandle);
            Marshal.WriteIntPtr(handleList, IntPtr.Size * 2, stderrHandle);
            if (!UpdateProcThreadAttribute(
                attributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                handleList, new IntPtr(IntPtr.Size * 3), IntPtr.Zero, IntPtr.Zero))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Handle-list attribute failed");

            var startup = new STARTUPINFOEX();
            startup.StartupInfo.cb = Marshal.SizeOf(typeof(STARTUPINFOEX));
            startup.StartupInfo.dwFlags = (int)STARTF_USESTDHANDLES;
            startup.StartupInfo.hStdInput = stdinHandle;
            startup.StartupInfo.hStdOutput = stdoutHandle;
            startup.StartupInfo.hStdError = stderrHandle;
            startup.lpAttributeList = attributeList;
            var command = new StringBuilder("\"" + executable + "\" " + role);
            created = CreateProcess(
                executable,
                command,
                IntPtr.Zero,
                IntPtr.Zero,
                true,
                EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW,
                IntPtr.Zero,
                workingDirectory,
                ref startup,
                out processInfo);
            if (!created)
                throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateProcess failed");

            if (WaitForSingleObject(processInfo.hProcess, INFINITE) == UInt32.MaxValue)
                throw new Win32Exception(Marshal.GetLastWin32Error(), "WaitForSingleObject failed");
            uint exitCode;
            if (!GetExitCodeProcess(processInfo.hProcess, out exitCode))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "GetExitCodeProcess failed");
            return unchecked((int)exitCode);
        }
        finally
        {
            if (created && processInfo.hProcess != IntPtr.Zero)
            {
                uint ignored;
                if (GetExitCodeProcess(processInfo.hProcess, out ignored) && ignored == STILL_ACTIVE)
                    TerminateProcess(processInfo.hProcess, 1);
            }
            if (processInfo.hThread != IntPtr.Zero) CloseHandle(processInfo.hThread);
            if (processInfo.hProcess != IntPtr.Zero) CloseHandle(processInfo.hProcess);
            if (attributeListInitialized) DeleteProcThreadAttributeList(attributeList);
            if (handleList != IntPtr.Zero) Marshal.FreeHGlobal(handleList);
            if (jobList != IntPtr.Zero) Marshal.FreeHGlobal(jobList);
            if (attributeList != IntPtr.Zero) Marshal.FreeHGlobal(attributeList);
            if (stderrHandle != IntPtr.Zero && stderrHandle != INVALID_HANDLE_VALUE) CloseHandle(stderrHandle);
            if (stdoutHandle != IntPtr.Zero && stdoutHandle != INVALID_HANDLE_VALUE) CloseHandle(stdoutHandle);
            if (stdinHandle != IntPtr.Zero && stdinHandle != INVALID_HANDLE_VALUE) CloseHandle(stdinHandle);
            if (job != IntPtr.Zero) CloseHandle(job);
        }
    }
}
'@
}

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
        $code = [FtmonTaskNativeJob]::RunInKillOnCloseJob(
            $Exe,
            $RoleName,
            (Get-Location).Path,
            $tempOut,
            $tempErr)

        foreach ($capture in @($tempOut, $tempErr)) {
            if ((Test-Path -LiteralPath $capture) -and (Get-Item -LiteralPath $capture).Length -gt 0) {
                Get-Content -LiteralPath $capture -Encoding utf8 | ForEach-Object {
                    Add-Content -LiteralPath $LogPath -Encoding utf8 -Value $_
                }
            }
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

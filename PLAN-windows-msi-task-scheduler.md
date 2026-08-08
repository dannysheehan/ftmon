# Issues #94 and #95 — Self-contained Windows MSI and automatic startup

## Summary

Deliver this as two dependent PRs:

1. **#94:** ship and test the per-user Task Scheduler helper through both PyPI and future frozen distributions.
2. **#95:** build a self-contained x64 PyInstaller directory, wrap it in a per-user WiX v7 MSI, and include the #94 helper.

The MSI installs without elevation under `%LOCALAPPDATA%\Programs\FTMON`. It does not automatically create or start tasks; the operator explicitly runs the packaged helper after `ftmon init`. The daemon task is the default, while persistent web remains opt-in. This avoids fragile MSI custom actions and keeps task ownership visibly under the current user.

PyPI/`uv` installation remains supported. The frozen ZIP should also be retained as the canonical payload later reusable by #96's Chocolatey package.

## Public interfaces and artifacts

- Task helper:

  ```powershell
  Install-FTMONTasks.ps1 `
    [-Action Install|Remove] `
    [-FtmonExe <absolute-path>] `
    [-IncludeWeb]
  ```

  - Default action: `Install`.
  - Install always creates or updates `FTMON daemon`.
  - `-IncludeWeb` additionally creates or updates `FTMON web`.
  - Installing without `-IncludeWeb` does not delete an existing web task.
  - `-Action Remove` idempotently stops and unregisters both official tasks.
  - Registration never silently starts or restarts a process.

- Release artifacts:

  - `ftmon-<PEP440-version>-windows-x64.zip`
  - `ftmon-<PEP440-version>-windows-x64.msi`
  - `SHA256SUMS.txt`

- Installed layout:

  ```text
  %LOCALAPPDATA%\Programs\FTMON\
  ├── ftmon.exe
  ├── _internal\...
  ├── Install-FTMONTasks.ps1
  ├── Invoke-FTMONTask.ps1
  ├── LICENSE
  └── THIRD_PARTY_NOTICES.txt
  ```

- The MSI adds the installation directory to the current user's `PATH`. Configuration and state remain exclusively in the existing platformdirs locations, never under the MSI installation directory.

## PR 1 — #94 Task Scheduler support

### PowerShell implementation

- Add `Install-FTMONTasks.ps1` and internal `Invoke-FTMONTask.ps1` under the Windows service-wrapper package area.
- Package both as Hatch `shared-scripts`, then verify a normal Windows `uv tool install` exposes them beside `ftmon.exe`.
- Resolve `ftmon.exe` from `Get-Command` unless `-FtmonExe` is supplied. Require an existing absolute path.
- Call `ftmon paths --json` and refuse installation until `config.toml` exists, with a direct instruction to run `ftmon init`.
- Copy the internal runner to `<state_dir>\tasks\`; task execution must not depend on a checkout or temporary package location.
- Reject execution as LocalSystem. Register tasks for the current user with:

  - account-specific `AtLogOn` trigger;
  - `Interactive` logon type;
  - `Limited` run level;
  - `IgnoreNew` multiple-instance policy;
  - indefinite execution time;
  - `StartWhenAvailable`;
  - battery start/continue enabled;
  - restart every minute, maximum 255 attempts;
  - stable working directory `<state_dir>\tasks`.

- Launch the runner with the absolute Windows PowerShell path, `-NoProfile`, `-NonInteractive`, `-WindowStyle Hidden`, and `RemoteSigned`. Keep the task definition visible in Task Scheduler; "hidden" applies to console windows, not operator discoverability.
- The runner accepts only `daemon` or `web`, invokes the absolute FTMON executable synchronously, and returns its exact exit code.
- Write wrapper output to separate daemon/web task logs. Before each start, roll a log larger than 1 MiB to one `.1` backup. The daemon's own rotating log remains authoritative.
- The web task executes exactly `ftmon web`; loopback binding remains enforced by application code.
- PM-02 remains the second duplicate-instance boundary if someone starts a daemon outside Task Scheduler.
- Do not create any MCP task.

### Documentation

Document:

- PyPI and source-checkout helper discovery.
- Daemon-only installation and explicit `-IncludeWeb`.
- Start, stop, restart, enable, disable, status, logs, and removal.
- `Get-ScheduledTask`, `Get-ScheduledTaskInfo`, `ftmon status`, `ftmon doctor`, and Task Scheduler Operational history.
- Logon-trigger semantics: monitoring begins only when the owning account logs on.
- Upgrade ordering: stop tasks, upgrade FTMON, rerun the helper to refresh the runner/path, start tasks, verify.
- Loopback web verification and SSH tunnelling.
- MCP as client-managed stdio.

## PR 2 — #95 frozen application and MSI

### Frozen x64 payload

- Pin **Python 3.12 x64** to an exact patch in one Windows packaging-version manifest.
- Pin **PyInstaller 6.20.0** in a Windows-packaging dependency group and `uv.lock`.
- Use a checked-in PyInstaller spec with `src/ftmon/__main__.py` as the console entry point and produce an `onedir` application.
- Explicitly collect:

  - all builtin and Windows profile TOML, including drafts;
  - SQLite migrations and scenarios;
  - web templates, static files, icons, and vendor licences;
  - MCP documentation resources;
  - pywin32 runtime DLLs and timezone support;
  - `windows-toasts`;
  - dynamically imported MCP and uvicorn modules;
  - the two #94 PowerShell scripts;
  - FTMON's licence and deterministically generated third-party notices.

- Do not include `extra-monitors`, development fixtures not used at runtime, caches, tests, or the legacy source.
- Keep console mode enabled because CLI and MCP use stdout/stdin. Task Scheduler hides its window through the runner rather than requiring a separate GUI-subsystem executable.
- Build with UPX disabled.
- Ensure all existing resource access works from the frozen layout; fix resource lookup centrally if any `Path(__file__)` assumptions fail.

### MSI authoring

- Pin **WiX Toolset v7.0.0** through a checked-in .NET tool manifest. Record acceptance of its OSMF v1.1 build-tool terms in the packaging rationale.
- Author an x64, `Scope="perUser"` package rooted at `PerUserProgramFilesFolder\FTMON`; no elevation and no machine-wide mode.
- Use one permanent product identity (`Id`/UpgradeCode equivalent) and a new ProductCode each build.
- Use an explicit major-upgrade rule scheduled `afterInstallInitialize` so a failed upgrade restores the previous payload. Block downgrades.
- Translate PEP 440 versions into monotonically increasing MSI versions:

  - build field = `patch * 1000 + channel`;
  - alpha `N` → `N`;
  - beta `N` → `200 + N`;
  - release candidate `N` → `400 + N`;
  - final → `800`;
  - post-release `N` → `800 + N`.

  Thus `2.0.0a15` becomes MSI `2.0.15`, while final `2.0.0` becomes `2.0.800`. Reject unsupported dev releases, prerelease numbers above 199, and values exceeding MSI numeric limits.

- Set the executable's numeric file version from the MSI mapping and its human-readable product version to the authoritative PEP 440 version.
- Install the complete PyInstaller directory, task helpers, licences, and notices.
- Add only the installation directory to the current user's `PATH`; uninstall removes only that exact PATH entry.
- Repair restores application files and PATH but never runs `ftmon init` or changes user state.
- Upgrade/uninstall never remove configuration, monitors, checks, actions, logs, or databases.
- The MSI does not create, start, stop, or remove Scheduled Tasks. Documentation requires `Install-FTMONTasks.ps1 -Action Remove` before MSI uninstall when tasks were configured.
- Do not automatically run FTMON during MSI install. This prevents an install transaction from migrating a database or starting a partially verified daemon.

### Upgrade and rollback contract

- Before upgrading:

  1. stop daemon/web tasks;
  2. run `ftmon doctor --backup <explicit-path>`;
  3. install the newer MSI;
  4. rerun the task installer;
  5. start and verify.

- Windows Installer rolls the application payload back automatically if the MSI transaction fails.
- Manual downgrade remains blocked. To roll back deliberately:

  1. remove startup tasks;
  2. uninstall the newer MSI;
  3. install the earlier MSI;
  4. restore the pre-upgrade database backup if the newer binary performed a schema migration;
  5. reinstall/start tasks and run `ftmon doctor`.

## CI, release, and acceptance tests

### Task-helper tests

- Parse both scripts with Windows PowerShell in Windows CI without registering tasks.
- Exercise the runner using a temporary fake executable and prove:

  - exact `daemon`/`web` argument forwarding;
  - output capture and rollover;
  - child exit-code propagation.

- Contract-test current-user logon, limited token, `IgnoreNew`, indefinite lifetime, restart settings, battery settings, absolute commands, and web opt-in.
- Assert absence of SYSTEM, highest privileges, startup trigger, MCP, demo mode, and `0.0.0.0`.
- Mutation-check web opt-in, principal level, duplicate policy, lifetime, and exit propagation.

### Frozen/MSI smoke workflow

On `windows-latest`:

1. Build the onedir payload and MSI from a clean locked environment.
2. Install the MSI silently with verbose logging.
3. Start a new process with Python and `uv` removed from `PATH`.
4. Verify `ftmon --version`.
5. Use temporary `FTMON_*` paths and run Windows-profile `init` and `check`.
6. Start the real daemon, wait at least three cycles, verify status, then terminate it cleanly.
7. Start web on an unused port, request HTML and static assets, and assert it listens only on `127.0.0.1`.
8. Run doctor through a desktop configuration and prove toast dependency loading does not fail with an import error.
9. Use the test environment's MCP client to spawn installed `ftmon mcp`, complete initialization, list tools/resources, and read one packaged guide.
10. Repair the installation and repeat version/resource checks.
11. Uninstall and assert executable, install directory, and PATH entry are removed while temporary FTMON configuration/database state remains.

### MSI lifecycle tests

- Build synthetic "previous" and "current" MSI versions with the same product identity.
- Install previous, create state outside the installation directory, upgrade, and verify:

  - exactly one FTMON product remains registered;
  - current payload replaces old payload;
  - external state survives;
  - downgrade installation is rejected.

- Build a test-only current MSI that deliberately fails after old-product removal and verify Windows Installer restores the previous version.
- After two real MSI releases exist, the release workflow must also download the preceding release MSI and exercise an actual cross-release upgrade before publication.

### Native #94 checklist

Document and perform where a Windows host is available:

- daemon-only install creates no web task;
- daemon advances for three cycles;
- repeated starts produce no duplicate;
- forced daemon failure is restarted;
- web opt-in listens only on loopback;
- neither task opens a console;
- reboot plus account logon restores monitoring;
- removal leaves no FTMON task.

### Release pipeline

- Add the Windows build/smoke job before PyPI publication so a broken MSI prevents the entire tag release.
- Upload ZIP and MSI alongside sdist/wheel.
- Validate tag, Python package version, frozen executable version, and MSI mapped version agree.
- Generate SHA-256 checksums only after all artifacts are finalized.
- Add an Authenticode script that signs `ftmon.exe` before MSI construction and signs the final MSI afterward using `signtool`, with RFC 3161 timestamping and `signtool verify /pa`.
- Keep signing conditional until certificate/Azure Trusted Signing credentials are configured; never print certificate secrets. Document unsigned prerelease behavior explicitly.
- Pin all GitHub Actions by commit SHA and record Python, PyInstaller, and WiX versions in build output.

## Specification and documentation

- Amend DO-02 to require the self-contained per-user Windows MSI, Task Scheduler lifecycle, silent installation, upgrades, rollback, and uninstall guidance.
- Bump SPEC to its next version, add a §21 entry for #94/#95, update DESIGN's companion version, and regenerate `tests/reqindex.json`.
- Update DESIGN's package tree and Windows service-wrapper rationale.
- Update README to lead Windows users to the MSI while retaining PyPI/`uv` as a supported alternative.
- Keep #96 out of scope, but make its future Chocolatey package consume the exact immutable ZIP produced here.

## Assumptions

- WiX v7 and its OSMF v1.1 terms are accepted by the project owner.
- The first MSI supports Windows x64 only and is strictly per-user.
- ARM64, per-machine installation, automatic updates, Chocolatey/Homebrew, and automatic MSI-managed task creation are deferred.
- Implementation starts from current `main` and preserves the Windows trust documentation from #108.

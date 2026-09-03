# Installing FTMON

FTMON monitors one desktop, workstation, or server. It must not run as root:
use the logged-in account on a desktop or a dedicated unprivileged account on
a server. Definitions and actions are intentionally confined to that account
(SE-01, PM-09).

## Install with uv

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) are required.

Install the pinned `uv` release as the account that will run FTMON when `uv`
is not already available:

```sh
curl -LsSf https://astral.sh/uv/0.11.28/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

### From PyPI (recommended)

Install the published package into an isolated tool environment, then
initialize and smoke-check:

```sh
uv tool install ftmon
ftmon init --profile desktop
ftmon check
```

This core install includes the daemon, CLI, and web UI without the MCP SDK's
authentication dependency tree. If this host will run `ftmon mcp`, install the
optional integration instead:

```sh
uv tool install 'ftmon[mcp]'
```

`pipx install ftmon` is an equivalent isolated installer if you prefer pipx.
PyPI's project page shows `pip install ftmon` in the sidebar; prefer
`uv tool` or `pipx` so the CLI is not mixed into a shared environment.

FTMON is published at <https://pypi.org/project/ftmon/>. The current series is
pre-release; pin explicitly when you need a fixed version (use the version
shown on PyPI):

```sh
uv tool install 'ftmon==X.Y.Z'
```

### From a source checkout

Use a checkout for unreleased commits, packaging work, or the public-demo
procedure (that path installs a root-owned copy so system deployment does not
depend on one administrator's home directory):

```sh
git clone https://github.com/dannysheehan/ftmon.git
cd ftmon
uv tool install .
ftmon init --profile desktop
ftmon check
```

For development, use `uv sync --extra mcp` followed by `uv run ftmon ...` so
the complete test suite can exercise the optional server. `ftmon init`
creates private directories, installs eight built-in monitor definitions (the
Linux `desktop` profile uses calibrated thresholds documented in
[docs/tuning-desktop-xps15.md](tuning-desktop-xps15.md)), and writes explicit
desktop notification settings. Extra monitors from `extra-monitors/` are
installed separately with `ftmon recipe install` and never ship inside the
core package wheel. For a headless host use
`ftmon init --profile server`; it writes the same ordinary configuration with
desktop popups disabled. Desktop/user initialization also creates an empty,
private `checks.toml` registry for external checks. Profiles only scaffold a
new `config.toml`—they do not
become a hidden runtime mode. Running init again preserves existing settings;
`--force` replaces built-in definitions only (PM-08).

The generic `desktop` and `server` names automatically select the calibrated
monitor tree for the current operating system. On Windows they resolve to
`windesktop` and `winserver`; on macOS they resolve to `macdesktop` and
`macserver`. The explicit platform names remain available for automation.

Windows external checks use SID ownership and NTFS DACLs rather than POSIX
uids and mode bits. In particular, stock `C:\Windows\System32` executables are
usually TrustedInstaller-owned and are deliberately rejected as direct check
executables. Before adding an alias to `checks.toml`, follow the
[Windows check location and trust guidance](check-authoring.md#windows-why-system32-executables-are-rejected)
and verify the candidate with `ftmon check trust <absolute-path>`; the command
does not execute it.

On macOS choose `macdesktop` (file audit plus best-effort Notification Center
delivery) or `macserver` (file audit only). Desktop notifications appear under
**Script Editor** in System Settings because the zero-bundle adapter uses
`/usr/bin/osascript`; exit zero means accepted, not proof that Focus displayed
a banner.

The macOS profile enables a source-filtered unified-log monitor for third-party
executable faults and explicit kernel storage-integrity messages. It
does not ingest the ambient debug stream or apply a blanket `severity >= error`
rule because routine Apple services emit error-level diagnostics. Writable visible volumes are monitored,
while read-only/nobrowse disk images are excluded.
See [macOS monitoring rationale](macos-monitoring.md) for the rule-by-rule
selection and deliberately deferred Apple-native signals.

### macOS (standalone uv + launchd)

On macOS, install standalone `uv` and FTMON into the account that will own the
monitor state. If you are building from a checkout instead of PyPI, the same
commands work; just replace `uv tool install ftmon` with `uv tool install .`
from the repository root.

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

uv tool install ftmon
ftmon init --profile macserver    # or: ftmon init --profile macdesktop
ftmon check
```

The core install is wheel-only on supported Intel and Apple Silicon macOS
versions and does not pull `cryptography`. If this host needs MCP, install
`'ftmon[mcp]'`; current MCP authentication dependencies may require a native
Rust/OpenSSL build on Intel. Prefer the standalone `uv` installer above on
older Intel releases, where asking Homebrew for `uv` can trigger a much larger
unsupported-host source-build chain.

For persistent per-user services, render the bundled launchd plist templates
into `~/Library/LaunchAgents/` with the actual `ftmon` path and log directory
for this account. The repository templates live in `src/ftmon/launchd/`.

```sh
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
FTMON_BIN="$(command -v ftmon || echo "$HOME/.local/bin/ftmon")"

for name in daemon web; do
  sed \
    -e "s|/Users/REPLACE_ME/.local/bin/ftmon|$FTMON_BIN|" \
    -e "s|/Users/REPLACE_ME/Library/Logs|$HOME/Library/Logs|" \
    "src/ftmon/launchd/org.ftmon.${name}.plist" \
    > "$HOME/Library/LaunchAgents/org.ftmon.${name}.plist"
done

plutil -lint "$HOME/Library/LaunchAgents/org.ftmon.daemon.plist"
plutil -lint "$HOME/Library/LaunchAgents/org.ftmon.web.plist"

launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/org.ftmon.daemon.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/org.ftmon.web.plist"

launchctl print "gui/$(id -u)/org.ftmon.daemon" >/dev/null
launchctl print "gui/$(id -u)/org.ftmon.web" >/dev/null
```

The web UI stays on the CLI's loopback-only default. To reach it remotely, use
an SSH tunnel such as `ssh -L 8420:127.0.0.1:8420 <host>`. The daemon writes its
rotating operational log to the `ftmon paths` `log_file`; launchd also captures
failures before that logger starts in `~/Library/Logs/ftmon-daemon-launchd.log`.
Web process output goes to `~/Library/Logs/ftmon-web.log`.

Send SIGHUP to the daemon's managed PID to reload in place. `launchctl kickstart
-k` is an explicit restart with a new PID, not a reload substitute.

### Windows (MSI + Task Scheduler)

On Windows, the recommended operator install is the per-user x64 MSI from the
GitHub Release (`ftmon-<version>-windows-x64.msi`). It needs no elevation and
installs under `%LOCALAPPDATA%\Programs\FTMON`, adding that directory to the
current user's `PATH`. Configuration, monitors, checks, actions, logs, and the
database stay in the normal platformdirs locations — never under the MSI
directory.

Silent install / repair / uninstall:

```powershell
msiexec /i ftmon-<version>-windows-x64.msi /qn
msiexec /fa ftmon-<version>-windows-x64.msi /qn   # repair files + PATH only
msiexec /x ftmon-<version>-windows-x64.msi /qn
```

Repair restores application files and PATH; it never runs `ftmon init` and never
changes user state. Ordinary uninstall removes the install directory and that
exact PATH entry while leaving configuration and databases intact. Before
uninstalling an MSI that had startup tasks configured, remove them first:

```powershell
Install-FTMONTasks.ps1 -Action Remove
```

Prerelease MSIs may be unsigned until Authenticode / Azure Trusted Signing
credentials are configured. Windows SmartScreen may warn on first run; that is
expected for unsigned builds.

PyPI / `uv` remains supported when you already manage a Python toolchain:

```powershell
uv tool install ftmon
ftmon init --profile desktop    # selects windesktop on Windows
ftmon check
```

After either install path, initialize once, then register Task Scheduler
startup. The helpers ship beside `ftmon.exe` (MSI layout and `uv tool`
`Scripts\`) and as package data under `ftmon/windows/`:

```powershell
ftmon init --profile desktop
# Daemon at logon (default). Does not start the process.
Install-FTMONTasks.ps1
# Optional persistent web (loopback-only):
# Install-FTMONTasks.ps1 -IncludeWeb

Start-ScheduledTask -TaskName 'FTMON daemon'
# Start-ScheduledTask -TaskName 'FTMON web'
```

From a source checkout without an installed script on `PATH`:

```powershell
powershell -NoProfile -ExecutionPolicy RemoteSigned `
  -File .\src\ftmon\windows\Install-FTMONTasks.ps1 `
  -FtmonExe (Get-Command ftmon).Source
```

Installing without `-IncludeWeb` does not delete an existing web task.
`-Action Remove` stops and unregisters both official tasks idempotently.
Registration never silently starts or restarts a process. Tasks use an
account-specific logon trigger, Interactive logon, Limited run level, and
IgnoreNew multiple-instance policy — monitoring begins only when the owning
account logs on. MCP stays client-managed stdio; there is no MCP task.

Lifecycle commands:

```powershell
Get-ScheduledTask -TaskName 'FTMON*'
Get-ScheduledTaskInfo -TaskName 'FTMON daemon'
Start-ScheduledTask -TaskName 'FTMON daemon'
Stop-ScheduledTask -TaskName 'FTMON daemon'
Disable-ScheduledTask -TaskName 'FTMON daemon'
Enable-ScheduledTask -TaskName 'FTMON daemon'
ftmon status
ftmon doctor
```

Wrapper logs roll under the `ftmon paths` `state_dir` as `task-daemon.log` and
`task-web.log` (1 MiB → `.1` backup). The daemon's own rotating log remains
authoritative. Task Scheduler Operational history is under Event Viewer →
Applications and Services Logs → Microsoft → Windows → TaskScheduler →
Operational.

Upgrade ordering (MSI or `uv tool upgrade`):

1. `Stop-ScheduledTask` for daemon/web
2. `ftmon doctor --backup <explicit-path>`
3. install the newer MSI / upgrade the tool
4. re-run `Install-FTMONTasks.ps1` (refreshes the runner copy and absolute path)
5. `Start-ScheduledTask` and verify with `ftmon status` / `ftmon doctor`

`Stop-ScheduledTask` terminates the wrapper and its FTMON child process tree.
Before upgrading, confirm that no stale `ftmon.exe daemon` or `ftmon.exe web`
process remains; v2.0.0a17 and earlier could leave those children running.

Windows Installer rolls the application payload back automatically if an MSI
upgrade transaction fails. MSI downgrades are blocked by design. To roll back
deliberately to an earlier release:

1. `Install-FTMONTasks.ps1 -Action Remove` (stop and unregister startup tasks)
2. uninstall the newer MSI (`msiexec /x … /qn`)
3. install the earlier MSI
4. restore the pre-upgrade database backup if the newer binary may have run a
   schema migration (`ftmon doctor` / the backup from step 2 of upgrade)
5. re-run `Install-FTMONTasks.ps1`, start the tasks, and verify with
   `ftmon status` / `ftmon doctor`

Native verification checklist (run on a real Windows host when changing Task
Scheduler helpers or MSI packaging). Record evidence with:

```powershell
# Performs native observations (duplicate start, no console, three ticks,
# forced restart, task-stop child cleanup, web loopback, remove).
# reboot_logon_recovery stays pending.
uv run python tools/windows/record_native_checklist.py --observe

# After a real reboot+logon with tasks installed, edit the evidence file
# (set reboot_logon_recovery: pass) — --strict re-reads the file — or:
#   $env:FTMON_CHECKLIST_REBOOT_LOGON = 'pass'
uv run python tools/windows/record_native_checklist.py --strict `
  --evidence soak/windows-native/checklist-<stamp>.txt
```

Evidence files land under `soak/windows-native/` (gitignored). Config tokens
(`IgnoreNew`, `WindowStyle Hidden`) are recorded separately and do **not**
satisfy the observed duplicate-start / no-console fields. After `--observe`,
re-install the daemon task before a reboot so logon recovery can be verified:

```powershell
Install-FTMONTasks.ps1   # leave registered; Start-ScheduledTask optional
# reboot, log on as the task owner, confirm ftmon status advances, then either
# edit reboot_logon_recovery: pass in the evidence file, or:
$env:FTMON_CHECKLIST_REBOOT_LOGON = 'pass'
uv run python tools/windows/record_native_checklist.py --strict `
  --evidence soak/windows-native/checklist-<stamp>.txt
```

CI smoke covers install/ticks/loopback/MCP but not forced restart or
reboot+logon recovery.

Checklist items:

- daemon-only install creates no web task; `-IncludeWeb` adds it
- daemon advances for at least three cycles under Task Scheduler
- repeated `Start-ScheduledTask` produces no duplicate process (`IgnoreNew`)
- a forced daemon failure is restarted by the task settings
- web opt-in listens only on loopback (not `0.0.0.0`)
- neither task opens a console window
- reboot plus account logon restores monitoring
- `-Action Remove` leaves no `FTMON*` scheduled task

The web UI remains loopback-only (`http://127.0.0.1:8420/`). Reach it remotely
with an SSH tunnel such as `ssh -L 8420:127.0.0.1:8420 <host>`.

## Upgrade

Upgrading replaces the installed `ftmon` executable. Configuration,
`checks.toml`, monitor files, and the SQLite database are preserved. Schema
migrations run automatically the next time a process opens the database
(VC-01); do not copy a live `ftmon.db` file.

Check the running version, then upgrade from PyPI or from a checkout:

```sh
ftmon --version
uv tool upgrade ftmon
# or, from a git checkout of this repository:
# uv tool install --force .
```

Restart the daemon so it loads the new binary. Upgrading the tool does not
reload a running process:

```sh
# desktop / workstation user service
systemctl --user restart ftmon.service
systemctl --user status ftmon.service

# dedicated single-server service
# sudo systemctl restart ftmon.service
# sudo systemctl status ftmon.service
```

Verify with `ftmon --version` and `ftmon doctor`.

Do not re-run `ftmon init` unless you intend to refresh scaffolding.
`ftmon init --force` reinstalls built-in monitor TOML files only; it does not
upgrade the package (PM-08, FS-02). Extra-monitor recipes are separate from
the core wheel: upgrading FTMON does not reinstall recipes under
`extra-monitors/`. After a release that changes sampling or baseline behaviour,
`ftmon baseline reset <monitor>` is available when learned normals are no
longer meaningful — see the [user manual](manual.md).

The synthetic demo website has its own update and rollback procedure under
[Publish the synthetic demo website](#publish-the-synthetic-demo-website).

## Notification credentials

Remote channels are disabled in the generated configuration. Credentials are
referenced through an environment variable or a protected file, never stored
literally in `config.toml`. For example:

```toml
[notify.ntfy]
enabled = true
min_severity = "warning"
base_url = "https://ntfy.sh"
topic = "my-server"
token_file = "/run/credentials/ftmon.service/ntfy-token"
```

Use exactly one of `token_env`/`token_file`, `url_env`/`url_file` for a webhook,
or `password_env`/`password_file` for SMTP. Credential files must be regular,
owned by the FTMON account, and inaccessible to group/other users (typically
mode 0600). Symlinks, oversized files, literal secret keys, missing references,
and unsafe permissions disable only that channel and produce a redacted config
warning (SE-05, NO-10).

The file notification audit remains mandatory. Enabled channels receive
independent durable delivery records, so success in one cannot hide failure in
another. Remote failures retry after 30 seconds, 2 minutes, 10 minutes, 1 hour,
then every 6 hours, with a 24-hour limit; file audit failures keep retrying.
HTTP 408/429/5xx and SMTP 4xx responses retry, while other HTTP 4xx and SMTP
5xx responses fail permanently (NO-07).

The generic webhook receives the versioned `ftmon.notify.v1` JSON document.
Its full URL is a secret because many messenger services embed credentials in
the path or query:

```toml
[notify.webhook]
enabled = true
min_severity = "error"
url_env = "FTMON_WEBHOOK_URL"
```

SMTP always establishes STARTTLS or implicit TLS before authentication:

```toml
[notify.smtp]
enabled = true
min_severity = "warning"
host = "smtp.example.net"
port = 587
tls = "starttls"
username = "ftmon@example.net"
from = "ftmon@example.net"
to = ["operator@example.net"]
password_file = "/run/credentials/ftmon.service/smtp-password"
```

Notification bodies sent through ntfy, a webhook, or SMTP leave the monitored
host. Keep rule messages concise and avoid sensitive command lines or journal
content. The public ntfy service may retain messages temporarily; self-host ntfy
when that data-egress policy is unsuitable (NO-09).

`ftmon doctor` reports each channel as `ready`, `disabled`, or with a stable
error code. It resolves references and checks local readiness but deliberately
does not send a test notification or print credential values (NO-10).

## Run the daemon with systemd

### Desktop or workstation user service

The wheel contains `ftmon/systemd/ftmon.service`. With the repository checkout:

```sh
mkdir -p ~/.config/systemd/user
cp src/ftmon/systemd/ftmon.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ftmon.service
systemctl --user status ftmon.service
```

The packaged unit expects uv's default `~/.local/bin/ftmon` installation. If
you installed elsewhere, copy the unit and change only `ExecStart`. FTMON is a
user service because running as root would give monitor definitions and actions
authority they neither need nor should have.

The user daemon unit deliberately keeps `NoNewPrivileges=yes` but does not set
`PrivateTmp=yes`. For an unprivileged user service, systemd implements
`PrivateTmp` through a user namespace; processes owned by other host users then
appear as the overflow identity (`nobody`). That corrupts the process sampler's
`username` attribute and defeats username-based exemptions. The system-level
server unit can retain `PrivateTmp` because the privileged system manager can
create its mount namespace without UID remapping; non-sampling web/demo units
do not depend on process identity.

### Dedicated single-server service

On a headless server, use a dedicated system account rather than an
administrator's login account. A real home directory is intentional: FTMON's
configuration, SQLite database, notification audit, and optional action
allow-list need one private, predictable ownership boundary (PM-09, DO-06).

```sh
sudo useradd --system --create-home --home-dir /var/lib/ftmon \
  --shell /usr/sbin/nologin ftmon
sudo env UV_TOOL_DIR=/opt/ftmon UV_TOOL_BIN_DIR=/usr/local/bin \
  uv tool install ftmon
# From a checkout instead of PyPI: uv tool install .
sudo -u ftmon -H /usr/local/bin/ftmon init --profile server
sudo install -d -o root -g ftmon -m 0755 /etc/ftmon
printf '[check]\n' | sudo install -o root -g ftmon -m 0640 /dev/stdin \
  /etc/ftmon/checks.toml
sudo install -m 0644 src/ftmon/systemd/ftmon-server.service \
  /etc/systemd/system/ftmon.service
sudo systemctl daemon-reload
sudo systemctl enable --now ftmon.service
sudo systemctl status ftmon.service
```

Root owns `/opt/ftmon` and `/usr/local/bin/ftmon`, so the service account cannot
replace the program systemd starts. The example copies the unit from a source
checkout. Installed packages also contain `ftmon/systemd/ftmon-server.service`;
inspect it before installation and adjust `ExecStart` if the executable is
elsewhere. The unit fixes `User` and `Group` to `ftmon`, grants no capabilities
or unit-defined supplementary groups, makes the host filesystem read-only to
the process, and permits writes only below `/var/lib/ftmon`. These controls
limit the impact of a bad definition or action; they do not turn user-authored
actions into untrusted sandboxed code. A start is refused if server-profile
`config.toml` is absent, preventing an apparently healthy empty deployment.
The unit reads external-command authority from `/etc/ftmon/checks.toml`.
Root owns that file while group `ftmon` has read-only access; it is outside the
unit's writable paths so an editable monitor definition cannot grant a new
command. See [External checks](external-checks.md) before adding an alias.

The unit deliberately does not use `ProtectProc=invisible`. FTMON cannot
truthfully report other users' processes if systemd hides them. Linux may
still restrict individual process details through `/proc` mount options or
Yama; FTMON records unavailable optional fields as unavailable rather than
requiring root.

Journal visibility is also an explicit operator choice. With no extra group,
FTMON normally sees only records available to its account. If system-wide
journal monitoring matters more than that isolation, grant the narrow
platform-specific journal ACL or group (commonly `systemd-journal`) and record
that decision in the server's security documentation:

```sh
sudo usermod -aG systemd-journal ftmon
sudo systemctl restart ftmon.service
```

Group membership exposes potentially sensitive messages from unrelated
services. Do not add `ftmon` broadly to `sudo`, `adm`, container-engine, or
application groups as a shortcut. Prefer a targeted journal ACL where the
platform permits one. A narrowly scoped `sudoers` rule for one root-owned,
read-only external check is supported as an advanced exception; the exact
wrapper and validation rules are in [External checks](external-checks.md).

A recipe classified `service-socket` may use a rootless service socket already
owned by the same account running the per-user FTMON daemon. It does not make a
rootful container socket acceptable: never add the dedicated `ftmon` account to
`docker` or another container-engine group, change that socket's mode, or weaken
the packaged unit to expose it.

#### Credentials with systemd

For a system service, protected files or systemd credentials are preferred to
environment variables: environment values may be visible to service-management
tools and are easy to copy into diagnostics. Create an administrator-owned
source outside the repository and map it into the service's private credential
directory:

```sh
sudo install -d -m 0700 /etc/ftmon/credentials
sudo install -m 0600 /dev/stdin /etc/ftmon/credentials/ntfy-token
sudo systemctl edit ftmon.service
```

```ini
[Service]
LoadCredential=ntfy-token:/etc/ftmon/credentials/ntfy-token
```

Then configure `token_file = "/run/credentials/ftmon.service/ntfy-token"`.
Use the same pattern for `webhook-url` and `smtp-password`. `LoadCredential=`
copies each value into a service-private, read-only location; the source still
needs administrator-only permissions. Never put a token in `ExecStart`, an
`Environment=` line, the unit itself, or Git (SE-05).

#### Operations and remote dashboard access

```sh
sudo -u ftmon -H /usr/local/bin/ftmon doctor
sudo journalctl -u ftmon.service
```

The daemon does not serve the dashboard. Start the loopback-only web process
separately when interactive access is required:

```sh
sudo -u ftmon -H /usr/local/bin/ftmon web
```

From the administrator's workstation, create the tunnel while that process is
running:

```sh
ssh -N -L 8420:127.0.0.1:8420 server.example.net
```

After opening the tunnel, browse to <http://127.0.0.1:8420/> locally. Keep the
operational dashboard bound to loopback: it has no login boundary and includes
write operations (ack, approve draft, enable/disable monitor), so publishing it
through a reverse proxy is unsupported.

On a **shared-login** host — several administrators using one Unix account, or
a desktop where untrusted local processes may run — anyone who can reach the
loopback port can perform those writes. Bind the SSH forward to `127.0.0.1` on
your workstation (`ssh -L 127.0.0.1:8420:127.0.0.1:8420 …`), not `0.0.0.0`, and
treat an open tunnel like temporary root on the monitored host. This is deliberate
(NG-05): FTMON is a single-user, loopback-only management surface, not a
multi-tenant console.

The separate synthetic demo application is the only FTMON mode designed for a
public proxy.

Actions remain disabled unless a monitor explicitly names an executable that
the administrator placed in `/var/lib/ftmon/.config/ftmon/actions/`. Run such
scripts as `ftmon` during review and keep them unable to invoke privileged
helpers. The service hardening may intentionally prevent scripts that write
outside FTMON's state directories.

#### Test notification configuration without sending secrets externally

`ftmon check` validates channel shape and `ftmon doctor` resolves credential
references and reports readiness without sending a message. For an end-to-end
smoke test, point the generic webhook temporarily at a loopback-only HTTP
receiver, trigger a test incident from a temporary definition, and verify both
the received `ftmon.notify.v1` document and
`~ftmon/.local/state/ftmon/notifications.jsonl`. This tests fan-out and the
durable audit without contacting the Internet. Restore the real reference and
restart the service afterwards. Do not use production tokens in test fixtures
or paste request bodies into issue reports (TS-13).

#### Soak evidence (pre-v1.0)

Release readiness (TS-17) requires weekly evidence from long-running hosts. See
[soak procedure](soak-procedure.md). On a server-profile install, copy
`tools/capture_soak_evidence.sh` to `/opt/ftmon/bin/`, install
`ftmon-soak-evidence.service` and `ftmon-soak-evidence.timer` from
`src/ftmon/systemd/`, and enable the timer. Evidence lands under
`/var/lib/ftmon/soak/evidence/`; keep host manifests private (not in Git).

## Web dashboard

```sh
ftmon web
```

Open <http://127.0.0.1:8420/>. The service binds only to loopback and uses no
remote assets. Keep the hostname consistent (`127.0.0.1` or `localhost`) so
the write-operation Origin check can protect against DNS rebinding and CSRF.
The **Trends** page graphs declared growth profiles such as disk capacity and
process memory growth; monitor and incident pages link into the same explorer.

## Publish the synthetic demo website

The reference deployment is live at
[demo.ftmon.org](https://demo.ftmon.org/). This procedure reproduces that
public, read-only demonstration. **Never pass a real operational `ftmon.db`,
its backup, or a copy of host configuration to demo mode.** The application
rejects unmarked databases, but deployment separation is the primary safety
control: use a dedicated machine or account with no access to an operational
FTMON home (UI-15, SE-06, DO-06).

### 1. Prepare DNS and the host

Create an `A` record for `demo.ftmon.org` and an `AAAA` record only when IPv6
is correctly routed. Point them at the public host, allow inbound TCP 80 and
443, and keep port 8420 blocked externally. Caddy needs 80/443 to obtain and
renew certificates; the FTMON backend remains on loopback so bypassing TLS and
the hosting controls is impossible.

#### Install the Ubuntu/Debian prerequisites

The public demo needs `uv` to install FTMON, the stock Caddy package for its
service account and systemd unit, and `Go` plus `xcaddy` to compile the pinned
rate-limit module. Go and `xcaddy` are build-time tools; the running services do
not need them after `/usr/local/bin/caddy-ftmon-demo` has been installed.

Install basic download and repository tools first:

```sh
sudo apt update
sudo apt install -y ca-certificates curl git gpg \
  debian-keyring debian-archive-keyring apt-transport-https
```

Install a pinned `uv` release with Astral's official installer, then copy the
verified user installation into the system administrator's path. Pinning keeps
deployment rebuilds repeatable; update the version deliberately rather than
silently following the latest installer.

```sh
curl -LsSf https://astral.sh/uv/0.11.28/install.sh | sh
"$HOME/.local/bin/uv" --version
sudo install -o root -g root -m 0755 "$HOME/.local/bin/uv" /usr/local/bin/uv
uv --version
```

Caddy 2.11.4 requires Go 1.25.1 or newer. The commands below install Go 1.26.5
from the official archive and verify its published checksum. Select the block
matching `uname -m`; do not use an older distribution Go package merely because
it is convenient.

For `x86_64`:

```sh
cd /tmp
curl -fLO https://go.dev/dl/go1.26.5.linux-amd64.tar.gz
echo '5c2c3b16caefa1d968a94c1daca04a7ca301a496d9b086e17ad77bb81393f053  '\
  'go1.26.5.linux-amd64.tar.gz' | sha256sum -c -
sudo rm -rf /usr/local/go
sudo tar -C /usr/local -xzf go1.26.5.linux-amd64.tar.gz
```

For `aarch64`/`arm64`:

```sh
cd /tmp
curl -fLO https://go.dev/dl/go1.26.5.linux-arm64.tar.gz
echo 'fe4789e92b1f33358680864bbe8704289e7bb5fc207d80623c308935bd696d49  '\
  'go1.26.5.linux-arm64.tar.gz' | sha256sum -c -
sudo rm -rf /usr/local/go
sudo tar -C /usr/local -xzf go1.26.5.linux-arm64.tar.gz
```

Expose Go system-wide and verify it before installing `xcaddy`:

```sh
echo 'export PATH=/usr/local/go/bin:$PATH' | \
  sudo tee /etc/profile.d/go.sh >/dev/null
export PATH=/usr/local/go/bin:$PATH
go version
```

Install the official stable Caddy package. The later custom binary deliberately
keeps this package's `caddy` account, directories, and hardened service unit.

```sh
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
  sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
  sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
  /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

Install `xcaddy` from its official Ubuntu/Debian repository. Although the
package provides the `xcaddy` executable, it still invokes the separately
installed Go compiler during each custom build.

```sh
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/xcaddy/gpg.key' | \
  sudo gpg --dearmor -o /usr/share/keyrings/caddy-xcaddy-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/xcaddy/debian.deb.txt' | \
  sudo tee /etc/apt/sources.list.d/caddy-xcaddy.list >/dev/null
sudo apt update
sudo apt install -y xcaddy
xcaddy version
```

If Caddy or `xcaddy` was already installed, compare the existing repository and
service configuration before overwriting it. These commands target a dedicated
Ubuntu/Debian demo host; follow the linked upstream installation references for
other operating systems.

Create a non-login account and install a root-owned program:

```sh
sudo useradd --system --create-home --home-dir /var/lib/ftmon-demo \
  --shell /usr/sbin/nologin ftmon-demo
sudo install -d -o root -g root -m 0755 /opt/ftmon-demo/bin /opt/ftmon-demo/tools
sudo env UV_TOOL_DIR=/opt/ftmon-demo/tools UV_TOOL_BIN_DIR=/opt/ftmon-demo/bin \
  uv tool install --force .
sudo install -d -o ftmon-demo -g ftmon-demo -m 0700 /var/lib/ftmon-demo
```

Keeping the root-owned demo release under `/opt/ftmon-demo` prevents the web
account from replacing the executable systemd starts and prevents a demo update
from colliding with an operational `/usr/local/bin/ftmon` installation. Do not
add this account to journal, application, container, or administrative groups;
synthetic demo mode needs no host telemetry, notification credentials, action
directory, MCP server, or daemon.

Run the install from a clean checkout of an exact signed release tag and record
its commit ID. Installing from a floating branch would make rebuilds and
rollback ambiguous even though the scenario itself is deterministic.

### 2. Install and build the synthetic snapshot

Install the checked-in demo service, builder, and timer artifacts once their
paths have been reviewed:

```sh
sudo install -m 0644 src/ftmon/systemd/ftmon-demo-build.service \
  /etc/systemd/system/
sudo install -m 0644 src/ftmon/systemd/ftmon-demo-web.service \
  /etc/systemd/system/
sudo install -m 0644 src/ftmon/systemd/ftmon-demo-refresh.service \
  /etc/systemd/system/
sudo install -m 0644 src/ftmon/systemd/ftmon-demo-refresh.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start ftmon-demo-build.service
sudo systemctl enable --now ftmon-demo-web.service ftmon-demo-refresh.timer
```

The builder runs the equivalent of:

```sh
sudo -u ftmon-demo /opt/ftmon-demo/bin/ftmon demo build \
  --output /var/lib/ftmon-demo/demo.db
```

It writes a versioned, seeded scenario to a temporary database, verifies its
coverage, fsyncs it, and atomically replaces `demo.db`. The web service opens
that completed file read-only and immutable. The timer rebuilds it regularly
to make releases and resets reproducible—not to clean visitor state, because
GET-only visitors cannot create any.

### 3. Put Caddy in front

Install Caddy using its [official service package](https://caddyserver.com/docs/install)
first, and install `xcaddy` from its
[official build instructions](https://github.com/caddyserver/xcaddy). The
package supplies the `caddy` account and service unit that the override below
deliberately retains. Create the bounded access-log directory explicitly so
configuration validation exercises the same path the service will use:

```sh
sudo install -d -o caddy -g caddy -m 0750 /var/log/caddy
sudo touch /var/log/caddy/ftmon-demo-access.log
sudo chown caddy:caddy /var/log/caddy/ftmon-demo-access.log
sudo chmod 0640 /var/log/caddy/ftmon-demo-access.log
```

The supplied configuration uses the rate-limit module pinned in its header.
Build that exact module revision with the stated Caddy version using `xcaddy`,
install the resulting root-owned binary, then install the site configuration.
Pinning makes this non-stock security dependency auditable and repeatable;
silently falling back to stock Caddy would remove the promised request limit.

```sh
xcaddy build v2.11.4 --output /tmp/caddy-ftmon-demo \
  --with github.com/mholt/caddy-ratelimit@5625512f24f6f59d6f64fb3aafe5eecff0b286db
/tmp/caddy-ftmon-demo list-modules | grep '^http.handlers.rate_limit$'
sudo install -o root -g root -m 0755 /tmp/caddy-ftmon-demo \
  /usr/local/bin/caddy-ftmon-demo
sudo install -m 0644 src/ftmon/deploy/Caddyfile.demo /etc/caddy/Caddyfile
sudo -u caddy -H /usr/local/bin/caddy-ftmon-demo validate \
  --config /etc/caddy/Caddyfile
sudo systemctl edit caddy
```

Do not continue if the build or module check fails. The explicit `/tmp` output
path avoids accidentally installing a stale `caddy` file from another working
directory. Validation runs as the service account so it catches filesystem
access problems without leaving root-owned runtime files; the service override
is added only after the expected binary and configuration both validate.

Use an override so distribution package upgrades cannot silently replace the
pinned custom binary:

```ini
[Service]
ExecStart=
ExecStart=/usr/local/bin/caddy-ftmon-demo run --environ --config /etc/caddy/Caddyfile
ExecReload=
ExecReload=/usr/local/bin/caddy-ftmon-demo reload --config /etc/caddy/Caddyfile
```

```sh
sudo systemctl daemon-reload
sudo systemctl enable caddy
sudo systemctl restart caddy
```

Caddy supplies automatic HTTPS and proxies only to `127.0.0.1:8420`; it does
not make an operational FTMON dashboard safe to publish. The demo backend also
checks the exact public Host, ignores forwarded authority, caps request targets,
and registers no write routes.

Rate and concurrency limiting are an explicit hosting boundary. The reference
configuration sets per-client and aggregate sliding-window limits and bounds
backend connections. An upstream CDN/load balancer is a valid alternative,
but its equivalent limits must be recorded and tested. Do not claim the
deployment is complete merely because TLS works.

### 4. Verify before announcing the URL

```sh
systemctl status ftmon-demo-web.service ftmon-demo-refresh.timer caddy
journalctl -u ftmon-demo-build.service -u ftmon-demo-web.service --since today
curl --fail --show-error https://demo.ftmon.org/
curl --fail --show-error https://demo.ftmon.org/trends
curl -I https://demo.ftmon.org/
curl -X POST -o /dev/null -w '%{http_code}\n' \
  https://demo.ftmon.org/incidents/1/ack
lychee --max-concurrency 4 --max-retries 2 https://demo.ftmon.org/
```

Confirm the persistent synthetic-data banner, `noindex,nofollow`, security
headers, clear/warning/error/disabled tiles, recovered and open incidents,
disk and memory-growth charts, chart gaps, and stale-data example. POST must be
404 or 405. Crawl the site with a bounded link checker and confirm it finds no
`/monitors`, `/self`, action, draft, backup, or MCP surface. Test the configured
rate/concurrency limit separately from a controlled address.

### 5. Update, roll back, and monitor

For an update, install the new root-owned package, rebuild to a new snapshot,
run the verification checklist, and only then restart the web service. Retain
the previous package version and its generated snapshot until verification
passes; rollback means restoring both together because scenario and reader
versions are validated as a pair. Never weaken the marker/version checks to
make an old database load.

Treat Caddy and its pinned rate-limit module as one release artifact. Rebuild,
validate, and restart the custom binary deliberately when either version
changes; an ordinary distribution Caddy upgrade does not update the binary
selected by the service override.

The generated database needs no backup: source scenario plus package version
reproduces it exactly, and visitor state does not exist. Back up deployment
configuration and release metadata instead. Monitor Caddy certificate renewal,
HTTP 5xx/latency and limit rejections, unit restarts/RSS, builder/timer failures,
disk space, and an external HTTPS/banner probe. Keep access logs on bounded
retention and avoid query-string retention when it is not operationally useful.

## MCP registration

Install the optional server before registering it:

```sh
uv tool install 'ftmon[mcp]'
```

Claude Code:

```sh
claude mcp add ftmon -- ftmon mcp
```

Claude Desktop configuration:

```json
{
  "mcpServers": {
    "ftmon": {
      "command": "/home/YOU/.local/bin/ftmon",
      "args": ["mcp"]
    }
  }
}
```

Replace `YOU` with the account name and restart Claude Desktop. MCP uses stdio;
it does not open another network listener. The SDK v2 server negotiates modern
and legacy MCP clients from this same command, so no compatibility-specific
registration is needed.

## Actions

Actions are an explicit local trust boundary. FTMON never creates, edits, or
changes permissions on files in `~/.config/ftmon/actions/` (AC-03). Create a
script yourself, review it, and make it executable before enabling a monitor
that references its bare filename:

```sh
install -m 0700 my-cleanup ~/.config/ftmon/actions/my-cleanup
ftmon check
```

Actions run only when an incident first opens, at most once per action every
ten minutes. They receive the documented `FTMON_*` environment, no arguments or
shell, and time out after 30 seconds. Output and exit status appear in incident
history.

## Database backups

```sh
ftmon doctor
ftmon doctor --deep
ftmon doctor --backup ~/ftmon-backup.db
```

Do not copy a live `ftmon.db` file. Committed rows may still be in SQLite's WAL;
`doctor --backup` uses SQLite's consistent backup API and verifies the result
(VC-03).

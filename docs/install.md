# Installing FTMON

FTMON monitors one desktop, workstation, or server. It must not run as root:
use the logged-in account on a desktop or a dedicated unprivileged account on
a server. Definitions and actions are intentionally confined to that account
(SE-01, PM-09).

## Install with uv

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) are required.

```sh
git clone https://github.com/dsheehan/ftmon.git
cd ftmon
uv tool install .
ftmon init --profile desktop
ftmon check
```

For development, use `uv sync` followed by `uv run ftmon ...`. `ftmon init`
creates private directories, installs eight editable built-in definitions, and
writes explicit desktop notification settings. For a headless host use
`ftmon init --profile server`; it writes the same ordinary configuration with
desktop popups disabled. Profiles only scaffold a new `config.toml`—they do not
become a hidden runtime mode. Running init again preserves existing settings;
`--force` replaces built-in definitions only (PM-08).

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

### Dedicated single-server service

On a headless server, use a dedicated system account rather than an
administrator's login account. A real home directory is intentional: FTMON's
configuration, SQLite database, notification audit, and optional action
allow-list need one private, predictable ownership boundary (PM-09, DO-06).

```sh
sudo useradd --system --create-home --home-dir /var/lib/ftmon \
  --shell /usr/sbin/nologin ftmon
sudo env UV_TOOL_DIR=/opt/ftmon UV_TOOL_BIN_DIR=/usr/local/bin \
  uv tool install .
sudo -u ftmon -H /usr/local/bin/ftmon init --profile server
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
services. Do not add `ftmon` to `sudo`, `adm`, container-engine, or application
groups as a shortcut. Prefer a targeted journal ACL where the platform permits
one.

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
write operations, so publishing it through a reverse proxy is unsupported.
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

## Web dashboard

```sh
ftmon web
```

Open <http://127.0.0.1:8420/>. The service binds only to loopback and uses no
remote assets. Keep the hostname consistent (`127.0.0.1` or `localhost`) so
the write-operation Origin check can protect against DNS rebinding and CSRF.
The **Trends** page graphs declared growth profiles such as disk capacity and
process memory growth; monitor and incident pages link into the same explorer.

## MCP registration

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
it does not open another network listener.

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

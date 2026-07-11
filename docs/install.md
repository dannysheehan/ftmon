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

## Publish the synthetic demo website

This procedure is only for a public, read-only demonstration at
`demo.ftmon.org`. **Never pass a real operational `ftmon.db`, its backup, or a
copy of host configuration to demo mode.** The application rejects unmarked
databases, but deployment separation is the primary safety control: use a
dedicated machine or account with no access to an operational FTMON home
(UI-15, SE-06, DO-06).

### 1. Prepare DNS and the host

Create an `A` record for `demo.ftmon.org` and an `AAAA` record only when IPv6
is correctly routed. Point them at the public host, allow inbound TCP 80 and
443, and keep port 8420 blocked externally. Caddy needs 80/443 to obtain and
renew certificates; the FTMON backend remains on loopback so bypassing TLS and
the hosting controls is impossible.

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
sudo /usr/local/bin/caddy-ftmon-demo validate --config /etc/caddy/Caddyfile
sudo systemctl edit caddy
```

Do not continue if the build or module check fails. The explicit `/tmp` output
path avoids accidentally installing a stale `caddy` file from another working
directory—the service override is added only after the expected binary and
configuration both validate.

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

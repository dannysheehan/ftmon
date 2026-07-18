# PostgreSQL local readiness

## Why

This recipe checks that one local PostgreSQL cluster accepts an authenticated
connection through its Unix socket. FTMON confirms failures across cycles,
keeps incident history and records connection latency for investigation.

The check connects and disconnects without running a query. That makes it a
readiness probe, not a database-performance or replication monitor. Connection
latency is useful in Metrics, but it is spiky rather than monotonic, so the
recipe does not invent a growth Trend.

## Install

Install PostgreSQL and the separately maintained Monitoring Plugins package on
Ubuntu/Debian:

```sh
sudo apt-get update
sudo apt-get install -y postgresql monitoring-plugins-standard
```

Create a peer-authenticated database role matching the OS account that runs
FTMON. The server-profile account is `ftmon`; substitute `id -un` for a
per-user installation. The role cannot create roles or databases and receives
only CONNECT on the dedicated health database:

```sh
sudo -u postgres createuser \
  --login --no-superuser --no-createdb --no-createrole ftmon
sudo -u postgres createdb --owner=postgres ftmon_health
sudo -u postgres psql \
  -c 'REVOKE ALL ON DATABASE ftmon_health FROM PUBLIC' \
  -c 'GRANT CONNECT ON DATABASE ftmon_health TO ftmon'
```

Do not add a password for this local check. PostgreSQL's default local peer
authentication verifies that the operating-system and database role names
match, so no secret enters FTMON configuration or process arguments.

## Configure

Review `checks.toml.example`. Replace the database role, database, socket
directory or port only when the local cluster differs, then install disabled:

```sh
ftmon recipe install postgres-ready --no-enable
ftmon check
ftmon monitor enable postgres_ready
```

The plugin warns above one second and becomes critical above three seconds.
Its own five-second timeout is shorter than FTMON's seven-second process-group
deadline, preserving a useful PostgreSQL diagnostic before the outer kill.

The only mapped perfdata label is the observed `time` value in seconds. No
query or dynamically named database metric is accepted.

## Test

Run the exact configured command as the FTMON user:

```sh
/usr/lib/nagios/plugins/check_pgsql \
  -H /var/run/postgresql -P 5432 \
  -d ftmon_health -l ftmon \
  -w 1 -c 3 -t 5
echo "$?"
```

Exit states are 0 OK, 1 warning, 2 critical and 3 unknown. The fixtures capture
check_pgsql 2.3.5 output observed against PostgreSQL 16.14: healthy and forced
warning connection latency, an absent socket port, and invalid socket input.

After registration, run `ftmon check` and `ftmon doctor`. A peer-authentication
failure is a configuration error; do not work around it by putting `-p` and a
password in argv.

## Security and permissions

This recipe has `service-socket` authority. `/var/run/postgresql` exposes a
local service endpoint, but PostgreSQL still authenticates the caller through
peer credentials. The database role is non-superuser, cannot create roles or
databases, has no password and receives only CONNECT on `ftmon_health`.

The command uses the Unix socket explicitly and does not open a TCP connection.
Do not add `-p`, weaken `pg_hba.conf` to `trust`, grant `pg_monitor` or execute a
query for this readiness-only recipe. Those are separate authority decisions.

## Upstream and licence

[Monitoring Plugins check_pgsql](https://www.monitoring-plugins.org/doc/man/check_pgsql.html)
is licensed `GPL-3.0-or-later` and remains separately installed. FTMON does not
redistribute the executable or copy upstream documentation.

Protocol verification on 2026-07-18 used check_pgsql 2.3.5 with PostgreSQL
16.14 on Ubuntu 24.04. The generic `ftmon`/`ftmon_health` example requires
operator substitution on per-user installations, so confidence remains
`tested` rather than `real-system-verified`.

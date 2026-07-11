# Why FTMON?

## One server still deserves monitoring

A hosted server, VPS, home server, workstation, or lab machine can suffer the
same slow failures as a fleet: disks fill, processes leak, certificates expire,
latency rises, services flap, and a warning scrolls out of view before anyone
looks. Real-time tools show what is happening now, but usually do not preserve
the incident and trend history needed to explain what happened overnight.

Full monitoring platforms solve that problem at fleet scale. They also bring a
collector, inventory, dashboards, authentication, deployment and ongoing
maintenance that may be disproportionate for one independently managed host.
FTMON occupies the space between those choices: one local daemon, one bounded
SQLite history, one declarative rule model, and notifications chosen by the
operator.

## More than an up/down check

An immediate check answers an important question: “is this service healthy
now?” FTMON adds time and lifecycle:

- confirmation prevents one transient result becoming an incident;
- escalation and recovery remain one explainable incident;
- bounded history shows what led up to the failure;
- baselines compare the host with its own normal behavior;
- declared Trends distinguish value, rate, confidence and projection;
- durable notification channels retry independently; and
- CLI, web and MCP surfaces investigate the same local evidence.

This is especially useful for performance data. A certificate check can report
days remaining, an HTTP check can report response time, and a UPS check can
report charge. The plugin can decide whether the current result is OK, warning
or critical; FTMON can additionally show whether the value is steadily getting
worse.

## Reuse checks instead of rebuilding a catalog

The external-check capability is deliberately compatible with small
user-written executables and the established Nagios plugin convention. FTMON
does not need to own implementations for every database, protocol, appliance or
sensor. Operators can reuse a separately installed check while FTMON supplies
the consistent history, incident, notification and Trend layer.

The boundary remains narrow:

- an administrator registers the exact executable and arguments;
- monitor definitions reference an alias rather than arbitrary commands;
- subprocesses run without a shell, inherited environment or root privilege;
- only explicitly declared performance labels become typed metrics;
- third-party code is not imported into the daemon or vendored into FTMON; and
- AI may compose a definition around approved authority but cannot create that
  authority.

This is not an attempt to recreate Nagios. There is no fleet inventory, remote
agent protocol, service discovery or central collector. Compatibility is at the
small local-check boundary: status, a useful message and optional numeric
performance data.

## Local-first does not mean isolated

FTMON keeps operational data on the monitored host and exposes no public
management listener. Administrators can still receive email, ntfy or webhook
alerts, inspect the dashboard through an SSH tunnel, and ask an AI assistant to
explain incidents through local stdio MCP. The operator chooses what leaves the
machine; monitoring does not require a cloud account.

## Available today

Single-host sampling, incidents, history, Trends, remote notifications, the
hardened server profile, synthetic public demo, administrator-registered local
checks, Nagios-compatible state/performance data and FTMON JSON checks are
implemented today. Compatibility remains deliberately bounded: FTMON executes
explicit local argv and does not discover plugins, vendor third-party code,
implement NRPE or become a fleet monitor.

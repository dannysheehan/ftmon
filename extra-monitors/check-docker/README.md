# Docker container state and restarts

## Why

This recipe confirms that one explicitly named Docker container exists, stays
running and has not entered a restart loop. FTMON adds consecutive-cycle
confirmation and incident history to the point-in-time Nagios result.

It deliberately creates one stable FTMON entity per configured container.
Run a separate alias and monitor definition for another container rather than
matching an unbounded fleet. `check_docker` cannot report state transitions
between polls, so restart/OOM event history remains a possible post-2.0 core
source question rather than a capability claimed by this recipe.

## Install

The separately maintained upstream project is
<https://github.com/timdaman/check_docker>. Version 2.3.0 is a standalone
Python 3 script with no runtime package dependency. Download the tagged file,
verify the observed digest, then install it as a regular trusted executable:

```sh
curl --fail --location --output /tmp/check_docker-2.3.0.py \
  https://raw.githubusercontent.com/timdaman/check_docker/2.3.0/check_docker/check_docker.py
echo "4b063667379efe781033d4f5c069684934a83384551c6575da331addc64006aa  /tmp/check_docker-2.3.0.py" | \
  sha256sum --check
sudo install -d -o root -g root -m 0755 /usr/local/lib/ftmon/checks
sudo install -o root -g root -m 0755 /tmp/check_docker-2.3.0.py \
  /usr/local/lib/ftmon/checks/check_docker
ftmon check trust /usr/local/lib/ftmon/checks/check_docker
```

Do not use a `pip install --user` console-script path without inspecting it:
environment-managed wrappers and symlinks commonly fail FTMON's exact-file
trust contract. The commands above install the reviewed tagged script without
copying it into this MIT repository.

## Configure

Confirm Docker is rootless for the same user that runs FTMON, then find its
socket without changing permissions:

```sh
docker context inspect --format '{{json .Endpoints.docker.Host}}'
stat /run/user/$(id -u)/docker.sock
```

Edit `checks.toml.example` before installation: replace UID `1000`, the
`example-app` anchored regular expression, and the matching monitor name and
entity. Then install it disabled for review:

```sh
ftmon recipe install check-docker --no-enable
ftmon check
ftmon monitor enable example_app_container
```

`--present` makes a missing named container critical. `--status running`
detects stopped or restarting state. `--restarts 1:3` warns after one automatic
restart and becomes critical at three. `--no-ok --no-performance` keeps output
bounded and avoids dynamic per-container perfdata labels that cannot form an
honest finite FTMON schema. Health is not enabled because containers without a
Docker `HEALTHCHECK` would become unknown; add `--health` only after verifying
the selected container defines one.

The plugin gets a 5-second socket timeout. FTMON's 7-second outer timeout leaves
room for its diagnostic, then kills the whole check process group if dockerd is
wedged.

## Test

Run the exact configured argv as the same user that runs FTMON:

```sh
/usr/local/lib/ftmon/checks/check_docker \
  --connection /run/user/1000/docker.sock \
  --timeout 5 \
  --containers '^example-app$' \
  --present --status running --restarts 1:3 \
  --no-ok --no-performance
echo "$?"
```

Exit states are 0 OK, 1 warning, 2 critical and 3 unknown. The fixtures capture
2.3.0 output observed in a temporary-container protocol POC: running with no
restarts, running after one automatic restart, stopped, and inaccessible
socket. The POC used a rootful workstation socket only to verify protocol
behaviour; that socket is not a supported FTMON deployment.

After registration, run `ftmon check` and `ftmon doctor`. A missing or
inaccessible rootless socket must remain unknown rather than being "fixed" by
granting broader authority.

## Security and permissions

This recipe has `service-socket` authority. The only supported path is a
rootless Docker socket already owned by the same user running the per-user
FTMON daemon. Do not add `ftmon` to the `docker` group, point the recipe at the
rootful `/var/run/docker.sock`, make a socket broadly readable/writable, expose
an unauthenticated TCP Docker API, or weaken the packaged service unit. Docker
API access is control authority, not a read-only monitoring capability.

The recipe supports Docker only. Podman's Docker-compatible API has not been
verified and is not claimed. The check contacts only the configured local Unix
socket; `--version` is intentionally absent so it does not query an image
registry.

## Upstream and licence

[check_docker](https://github.com/timdaman/check_docker) 2.3.0 is licensed
`GPL-3.0`. FTMON links to and separately installs the upstream executable; it
does not vendor its code or copy upstream documentation into this MIT
repository. Fixtures contain only observed protocol output.

Protocol POC on 2026-07-18 used check_docker 2.3.0 with Docker 29.1.3 on the
unfrozen workstation canary. A supported same-user rootless deployment remains
unverified, so the recipe confidence is `tested`, not
`real-system-verified`.

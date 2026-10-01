# NVIDIA GPU health

## Why

An NVIDIA GPU can run hot or saturated while the rest of the host appears
healthy. This recipe records one GPU's temperature, utilization, memory,
power, fan and clocks, and confirms sustained warning or critical states over
two samples. A failed probe opens a separate check-health incident instead of
claiming that the GPU is healthy.

## Install

This is an original FTMON-maintained MIT check adapted from the author's local
`~/.local/lib/ftmon/checks/check_gpu` script. It invokes the host's
`nvidia-smi`; install the NVIDIA driver and matching `nvidia-smi` package using
your distribution's supported driver procedure. Driver packages and binaries
are not included in this recipe.

Install the recipe script outside FTMON's data and state directories:

```sh
# Dedicated service host:
sudo install -d -o root -g root -m 0755 /usr/local/lib/ftmon/checks
sudo install -o root -g root -m 0755 \
  extra-monitors/nvidia-gpu/scripts/check_gpu \
  /usr/local/lib/ftmon/checks/check_gpu

# Single-user desktop: use ~/.local/lib/ftmon/checks instead, then replace
# argv[0] in checks.toml.example with its expanded absolute path.
```

Run `ftmon check trust /usr/local/lib/ftmon/checks/check_gpu` before registry
registration. The installed check needs Python 3.11+ and `nvidia-smi` in the
fixed PATH used by FTMON. Confirm the daemon user can invoke both; the recipe
does not request privileged GPU access.

## Configure

`ftmon recipe install nvidia-gpu` installs a disabled definition and a registry
example for review. Register the exact `[check.nvidia_gpu]` entry in the
administrator-owned `checks.toml`, then enable the monitor after checking that
GPU index 0 is the intended device. If selecting a different index, update
both `--index` and the stable `source_options.entity` identity. One definition
tracks one index; use distinct aliases and entities for additional GPUs.

The script's `--warn 80,95` and `--crit 90,98` compare temperature in °C and
GPU utilization in percent. Temperature limits are operational examples: use
your board's documented safe range and workload. High utilization can be
normal for compute jobs, so tune or disable that threshold where appropriate.
Keep the corresponding monitor parameters aligned with registry thresholds so
the glance tile reflects the configured temperature limits. The script gives
critical precedence if either reading crosses its critical threshold, even
when the other is only warning. Unknown state gets a separate warning rule.

The query requests ten numeric readings: `temp` (C), `util` and `mem_util`
(%), `mem_used` and `mem_total` (MiB), `power` and `power_limit` (W), `fan_pct`
(%), `sm_clock` and `mem_clock` (MHz). Each is mapped to a gauge in
`monitor.toml` without scaling. Unsupported or nonfinite cells are omitted;
missing readings do not become zero. No Trend is declared because workload,
clock, fan and temperature swings are expected, and a simple rising slope
would be misleading.

The check gives `nvidia-smi` three seconds and FTMON gives the whole script
five seconds, allowing time to encode a state-3 result. Both deadlines should
remain shorter than the sampling interval.

## Test

After installation, manually run the exact registry command as the FTMON
service user on the target host:

```sh
/usr/local/lib/ftmon/checks/check_gpu --index 0 --warn 80,95 --crit 90,98 --timeout 3
echo "$?"
ftmon check
ftmon doctor
```

The exit status must be 0; severity is JSON `state` (0 OK, 1 warning, 2
critical, 3 unknown). A state-3 result has empty `metrics`. A driver/library
mismatch may be printed by `nvidia-smi` on stdout or stderr; the check captures
the useful diagnostic, removes controls, and reports unknown. It never parses
CSV from a failed process as a healthy sample. The repository tests simulate
success, threshold crossings, recovery, unsupported cells, failure output and
timeout without accessing a GPU:

```sh
uv run pytest -q tests/extra_monitors/test_nvidia_gpu.py
```

## Security and permissions

The script reads local GPU telemetry through `nvidia-smi`; it needs no network,
credentials, `sudo`, or group grant. Keep its installed file root- or daemon-
owned, executable, symlink-free, and not group/world-writable. FTMON's runner
scrubs the environment and caps the outer process; the script also sets a
short inner deadline and emits one bounded JSON object. GPU model names and
driver diagnostics become local stored `plugin_message` text, so treat them as
host telemetry.

## Upstream and licence

The maintained source is this [FTMON recipe](https://github.com/dannysheehan/ftmon/tree/main/extra-monitors/nvidia-gpu),
licensed `MIT`. It derives from the author's existing local FTMON check, with
failure handling and direct tests added for this catalogue. The host's NVIDIA
driver and `nvidia-smi` remain separately supplied under their own licence.

Evidence status is `tested`: deterministic protocol fixtures and simulated
subprocess behavior have been validated. No NVIDIA driver version or GPU model
has been verified for this recipe, and `nvidia-smi` was not run while preparing
it. Verify the exact command and query fields on the target host before
enabling the monitor.

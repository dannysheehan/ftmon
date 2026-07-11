# Recipe title

## Why

Explain the failure or trend this catches and why local history adds value.

## Install

Link to the authoritative upstream project and give distribution-specific
installation examples. Do not download or vendor the plugin in this directory.

## Configure

Explain `checks.toml.example`, `monitor.toml`, every mapped label/UOM and the
chosen thresholds. Commands belong only in the administrator registry example.

## Test

Show how to invoke the exact argv as the FTMON user and interpret exit states,
stdout and fixture coverage. Network or hardware tests must be explicitly
manual or opt-in.

## Security and permissions

Describe network disclosure, credentials and privilege. Prefer `none`; when a
check genuinely needs root, use a root-owned wrapper and exact `sudoers` rule.

## Upstream and licence

Record the upstream URL, licence, version tested and date or environment of the
last real-system verification. FTMON does not redistribute the dependency.

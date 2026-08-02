# Windows native test baseline

Maintainer-facing audit record (DO-09), not user documentation — same
category as `docs/REVIEW-3.md` and `docs/drift-audit-m10.md`. Don't cite
from the manual or README.

## Purpose

`uv run pytest -q` has consistently produced the same 68 native-Windows
failures across several independent sessions of Windows platform work on
this repository (the DM-15 checkpoint fix, the cross-platform convergence
merge, and this audit). This document freezes that count into an
inspectable, categorized baseline so future runs can be diffed against it
instead of relying on session history or tribal memory of "the usual
Windows failures." A test suite run that produces a *different* 68 (or a
different total) than this baseline, on the same commit, is the signal
worth investigating — not the raw failure count by itself.

Tracking issue: [#79](https://github.com/dannysheehan/ftmon/issues/79).

This is an audit, not a fix plan. None of the 68 failures block the
DM-15/DM-19/DM-20 Windows work already merged; they are pre-existing gaps
in how the test suite itself was authored (POSIX-only fixtures, hardcoded
Linux paths, permission-model assumptions), not defects in product
behavior. Closing them is future work, tracked by the linked issue.

## Environment

| | |
|---|---|
| Branch / commit | `integration/platform-support` @ `1e5d384eb8098a6f6b57c241b153497fd511c570` |
| OS | Microsoft Windows 11 Pro (Build 26200) |
| Python | 3.12.3 |
| uv | 0.12.1 |
| Privilege | Standard (unelevated) user session, `SeCreateSymbolicLinkPrivilege` not held |
| Command | `uv run pytest -q -rf --tb=no` (full tracebacks captured separately per-test for categorization) |

## Summary

```
68 failed, 720 passed, 2 skipped, 5 deselected in 18.47s
```

Confirmed identical (same 68 test IDs, byte-for-byte) across three
independent full-suite runs in separate sessions on this commit lineage
before this audit, and reproduced again for this report.

## Issue #79 remediation progress

The original 68-failure baseline above remains the audit reference. The
`fix/windows-test-portability` branch records each native-Windows reduction
here so resolved categories stay distinguishable from hidden or renamed
failures.

| Checkpoint | Categories resolved | Native Windows result |
|---|---|---|
| Portable host/path/process fixtures | H, I, J | 56 failed, 732 passed, 2 skipped, 5 deselected |
| Native private-permission fixtures and process execution | B, D, K; exposed one additional C assertion | 26 failed, 761 passed, 3 skipped, 5 deselected |
| Portable controlled-clock transport and reload signaling | A, G | 14 failed, 773 passed, 3 skipped, 5 deselected |
| Unelevated symlink-fixture isolation | C | 8 failed, 777 passed, 9 skipped, 5 deselected |
| Optional inode-API fixtures | E | 4 failed, 781 passed, 9 skipped, 5 deselected |
| Windows-safe durable demo replacement | F | 785 passed, 9 skipped, 5 deselected |

The category labels above retain the original audit taxonomy, but remediation
confirmed two corrections to its hypotheses. Category E was test-only: product
code already omitted inode metrics when `os.statvfs` is absent, while four
fixtures incorrectly required that attribute to exist before monkeypatching it.
Category K was another Windows ACL-fixture failure, not a separate built-in
check defect. Resolving D also allowed the combined web-demo test to reach a
sixth privilege-dependent symlink assertion that the original five-item C list
had masked. Category F was a product defect: the demo builder used a read-only
handle for `fsync`, attempted POSIX directory `fsync`, and could not replace a
prior read-only artifact on Windows.

## Methodology

Every failing test's exception type and message were extracted from a
`-rf --tb=no` run. Categories below are grouped by **error signature**,
not by individually root-causing all 68 with a debugger. A subset — noted
per category — was independently confirmed by reading the actual fixture
and product source; the rest are inferred from the error type/message
matching a confirmed category's signature. Nothing here should be read as
"this exact line is the bug" for an unconfirmed test without checking it
first.

## Categories

| # | Category | Count | Root cause |
|---|---|---:|---|
| A | `socket.AF_UNIX` unavailable | 11 | e2e daemon tests use a Unix domain socket for single-instance locking/IPC; this Python's `socket` module on Windows has no `AF_UNIX`. |
| B | POSIX permission-bit assertions | 5 | Tests assert exact octal `st_mode` values (e.g. `33206 & 511 == 384`); `os.chmod`/`os.stat().st_mode` don't carry real POSIX semantics on Windows (NTFS ACLs, not mode bits). |
| C | Symlink creation needs elevated privilege | 5 | `os.symlink` requires `SeCreateSymbolicLinkPrivilege` (admin or Developer Mode); this session is unelevated → `OSError: [WinError 1314]`. |
| D | Windows ACL trust evaluator vs. POSIX-`chmod`-only fixtures | 25 | Fixtures call `.chmod(0o600)`/`.chmod(0o700)` intending "private." On Windows that's a no-op for real trust purposes — the newly-landed Windows ACL evaluator (EC-01/SE-07, `trusted_owner`/`writable_beyond_owner`) inspects the file's *actual* inherited ACL, which under `%TEMP%` is typically broader than the fixture assumes, so it correctly reports the file untrusted. Confirmed by reading `tests/unit/test_check_registry.py::_executable`/`_registry` and cross-referencing `checks/trust.py`'s Win32 DACL walk (see `DESIGN.md` §"checks/trust.py is the single evaluator..."). |
| E | `os.statvfs` doesn't exist on Windows | 4 | The disk-inode sampler path calls the POSIX-only `os.statvfs`; no Windows equivalent is wired into that code path yet. |
| F | Windows file-locking blocks delete/rename of an open handle | 4 | `demo.py`'s atomic-write-then-fsync-then-replace assumes POSIX semantics, where a still-open file can be renamed/unlinked. Windows locks the file while any handle is open. Traced directly: `demo.py:231`'s `os.fsync(stream.fileno())` runs after the stream was already closed (`OSError: [Errno 9] Bad file descriptor`), and the subsequent cleanup's `os.unlink()` then fails with `PermissionError: [WinError 5]` because the temp file is still locked. |
| G | `fcntl` module doesn't exist on Windows | 1 | A test imports the POSIX-only `fcntl` module directly rather than going through a platform seam. |
| H | Hardcoded Linux-only recipe executable paths | 10 | `extra-monitors/` recipe fixtures reference `/usr/local/lib/ftmon/checks/...` and `/usr/lib/nagios/plugins/...`; the registry-agreement test compares against these paths unconditionally, regardless of host platform. |
| I | POSIX shell-script test fixture | 1 | `test_runner_uses_fixed_environment_cwd_and_no_shell` authors its "check executable" as a `#!/bin/sh` script using `printf`/`$PWD`/`${VAR}` shell expansion — cannot execute on Windows at all, independent of trust state. Confirmed by reading the fixture. |
| J | Platform-declaration test assumes a Linux test host | 1 | `test_daemon_skips_monitor_not_declared_for_running_platform_pl_01` creates a monitor declared `platforms = ["windows"]` and asserts it is *skipped* — correct when the suite runs on Linux CI, inverted when run natively on Windows, where that monitor legitimately loads (correct PL-01 behavior). Confirmed by reading the fixture; this is a test-authoring gap, not a product bug. |
| K | Unconfirmed | 1 | `test_check_clean_builtins_if_importable` (`assert 1 in (0, 2)`, `ftmon check` against freshly-`ftmon init`'d builtins). Not independently root-caused in this pass; flagged for follow-up rather than guessed at. |

**Total: 68.**

None of A/B/C/E/G/H/I/J are specific to the Windows platform-support work
merged in this branch — they are latent gaps in test-suite portability
that simply never surfaced until the suite was run natively on Windows for
the first time. Category D is the largest bucket and the most directly
related to recent work, since it's a *consequence* of EC-01/SE-07 finally
having a real (and correctly stricter) Windows trust check instead of
silently no-op'ing — the tests need Windows-native fixture setup, not a
weaker check.

## Full failing-test list, by category

<details>
<summary>A — socket.AF_UNIX (11)</summary>

```
tests/e2e/test_daemon_e2e.py::test_leak_fire_and_clear_end_to_end
tests/e2e/test_daemon_e2e.py::test_kill9_at_most_one_duplicate_notification
tests/e2e/test_daemon_e2e.py::test_single_instance_lock
tests/e2e/test_daemon_e2e.py::test_episode_lifecycle_e2e
tests/e2e/test_daemon_e2e.py::test_quiet_hours_digest_e2e
tests/e2e/test_daemon_e2e.py::test_sigterm_stops_cleanly
tests/e2e/test_daemon_e2e.py::test_action_runs_through_real_daemon_once_e2e_ac_02
tests/e2e/test_daemon_e2e.py::test_disk_fill_rate_persisted_before_query_downsampling_ts_09_ca_09
tests/e2e/test_daemon_e2e.py::test_leak_profile_real_daemon_to_generic_http_ts_10
tests/e2e/test_daemon_e2e.py::test_sighup_reloads_without_exit_pm_11
tests/e2e/test_daemon_e2e.py::test_monitor_rescan_reloads_daemon_cl_07
```
</details>

<details>
<summary>B — POSIX permission-bit assertions (5)</summary>

```
tests/unit/test_cli.py::TestInit::test_init_creates_dirs_and_config
tests/unit/test_core.py::test_paths_env_overrides
tests/unit/test_core.py::test_atomic_write_modes_and_content
tests/unit/test_core.py::test_daemon_log_is_private_rotating_and_captures_process_messages
tests/unit/test_doctor.py::test_backup_uses_sqlite_snapshot_vc_03
```
</details>

<details>
<summary>C — symlink privilege (5)</summary>

```
tests/exchange/test_exchange.py::test_exchange_rejects_unsafe_links_symlinks_and_unmarked_replacement
tests/unit/test_check_registry.py::test_rejects_symlink_and_writable_registry_or_parent
tests/unit/test_cli.py::TestCheckTrust::test_symlink_rejected_cl_08
tests/unit/test_config_secrets.py::test_secret_file_rejects_symlink_and_embedded_controls
tests/unit/test_definitions.py::test_load_file_rejects_symlinks
```
</details>

<details>
<summary>D — Windows ACL trust vs. chmod-only fixtures (25)</summary>

```
tests/unit/test_actions.py::test_action_runner_minimal_env_output_and_rate_limit_ac_02
tests/unit/test_check_registry.py::test_loads_complete_immutable_registry_with_default_timeout
tests/unit/test_check_registry.py::test_rejects_invalid_entry_without_disclosing_argv[website_https-invalid_alias]
tests/unit/test_check_registry.py::test_rejects_invalid_entry_without_disclosing_argv[argv = ["EXEC"]\nprotocol = "shell"-invalid_protocol]
tests/unit/test_check_registry.py::test_rejects_invalid_entry_without_disclosing_argv[argv = ["EXEC"]\nprotocol = "nagios"\ntimeout = "31s"-invalid_timeout]
tests/unit/test_check_registry.py::test_rejects_invalid_entry_without_disclosing_argv[argv = ["relative"]\nprotocol = "nagios"-invalid_executable]
tests/unit/test_check_registry.py::test_rejects_unready_executable_and_protected_runtime_location
tests/unit/test_check_registry.py::test_invalid_replacement_does_not_mutate_previous_registry
tests/unit/test_cli.py::TestCheckTrust::test_trusted_executable_cl_08
tests/unit/test_config_secrets.py::test_secret_file_requires_private_regular_owned_file
tests/unit/test_external_checks.py::test_runner_rejects_untrusted_executable_and_caps_output
tests/unit/test_external_checks.py::test_runner_times_out_complete_check
tests/unit/test_external_integration.py::test_registered_json_metric_reaches_history_derived_rule_and_trend
tests/unit/test_mcp.py::TestDiscoverability::test_diagnose_external_alias_trust_mc_06
tests/unit/test_recipe_install.py::test_merge_recipe_checks_writes_protected_registry
tests/unit/test_recipe_install.py::test_install_recipe_enables_monitor_without_restart
tests/unit/test_recipe_install.py::test_install_recipe_accepts_explicit_directory_path
tests/unit/test_recipe_install.py::test_cli_recipe_install_and_check_install_alias
tests/unit/test_recipe_install.py::test_merge_recipe_checks_skips_existing_alias_without_force
tests/unit/test_recipe_install.py::test_merge_recipe_checks_rejects_invalid_existing_registry
tests/unit/test_recipe_install.py::test_install_recipe_no_enable_leaves_monitor_disabled
tests/unit/test_recipe_install.py::test_registry_accepts_masked_system_executable_owner
tests/unit/test_web_demo.py::test_demo_is_visibly_synthetic_get_only_and_immutable_ui_15_ts_14
tests/unit/test_web_demo.py::test_demo_exact_host_headers_forwarding_and_target_cap_se_06
tests/unit/test_web_demo.py::test_demo_rejects_unmarked_unsafe_and_symlink_databases_ui_15
```
</details>

<details>
<summary>E — os.statvfs (4)</summary>

```
tests/unit/test_samplers.py::test_disk_inode_used_pct_computation
tests/unit/test_samplers.py::test_disk_mount_options_expose_readonly_and_nobrowse_macos
tests/unit/test_samplers.py::test_disk_inode_omitted_when_f_files_zero
tests/unit/test_samplers.py::test_disk_deadline_stops_iteration
```
</details>

<details>
<summary>F — Windows file-locking on open handle (4)</summary>

```
tests/e2e/test_demo_builder.py::test_demo_build_cli_creates_immutable_marked_database_ui_15
tests/unit/test_demo.py::test_builder_marks_and_covers_the_synthetic_dataset_ui_15_ui_16
tests/unit/test_demo.py::test_builder_is_byte_deterministic_and_atomically_replaceable_ui_16
tests/unit/test_web_demo.py::test_demo_factory_accepts_the_real_seeded_builder_contract_ui_15_ui_16_ui_17
```
</details>

<details>
<summary>G — fcntl (1)</summary>

```
tests/unit/test_cli.py::TestMonitorRescan::test_rescan_signals_lock_holder_cl_07
```
</details>

<details>
<summary>H — hardcoded Linux recipe paths (10)</summary>

```
tests/extra_monitors/test_recipes.py::test_recipe_registry_and_monitor_agree_without_granting_authority[battery]
tests/extra_monitors/test_recipes.py::test_recipe_registry_and_monitor_agree_without_granting_authority[check-docker]
tests/extra_monitors/test_recipes.py::test_recipe_registry_and_monitor_agree_without_granting_authority[http-cert]
tests/extra_monitors/test_recipes.py::test_recipe_registry_and_monitor_agree_without_granting_authority[http-tls]
tests/extra_monitors/test_recipes.py::test_recipe_registry_and_monitor_agree_without_granting_authority[iowait]
tests/extra_monitors/test_recipes.py::test_recipe_registry_and_monitor_agree_without_granting_authority[package-updates]
tests/extra_monitors/test_recipes.py::test_recipe_registry_and_monitor_agree_without_granting_authority[postgres-ready]
tests/extra_monitors/test_recipes.py::test_recipe_registry_and_monitor_agree_without_granting_authority[root-disk]
tests/extra_monitors/test_recipes.py::test_recipe_registry_and_monitor_agree_without_granting_authority[sensors]
tests/extra_monitors/test_recipes.py::test_recipe_registry_and_monitor_agree_without_granting_authority[temperature]
```
</details>

<details>
<summary>I — POSIX shell-script fixture (1)</summary>

```
tests/unit/test_external_checks.py::test_runner_uses_fixed_environment_cwd_and_no_shell
```
</details>

<details>
<summary>J — platform-declaration test assumes Linux host (1)</summary>

```
tests/unit/test_m10_release.py::test_daemon_skips_monitor_not_declared_for_running_platform_pl_01
```
</details>

<details>
<summary>K — unconfirmed (1)</summary>

```
tests/unit/test_cli.py::TestCheck::test_check_clean_builtins_if_importable
```
</details>

## How to re-run this baseline

```sh
uv run pytest -q -rf --tb=no
```

Diff the `FAILED` lines against the lists above. An unchanged 68 on the
same commit lineage confirms nothing regressed; any difference (new
failures, or fewer than expected) is the signal to investigate — not the
raw count.

## Non-goals

This document does not propose fixes. Remediation (Windows-native fixture
setup for category D, a `Sampler`/`EventSource`-style seam for the
POSIX-only pieces in A/E/G, platform-conditional expectations for B/J,
portable recipe path resolution for H, POSIX-script-free fixtures for
C/I) is tracked by the linked issue and is deliberately out of scope here.

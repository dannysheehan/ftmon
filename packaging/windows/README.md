# Windows packaging rationale (issues #94 / #95)
#
# Task Scheduler helpers (Install-FTMONTasks.ps1 / Invoke-FTMONTask.ps1) are the
# PL-01 Windows service-wrapper seam. They ship as package data under
# src/ftmon/windows/ and as Hatch shared-scripts beside ftmon.exe.
#
# The frozen payload is a PyInstaller 6.20.0 onedir application pinned on
# CPython 3.12 x64 (see versions.toml). Console mode remains enabled because
# CLI and MCP need stdout/stdin; Task Scheduler hides windows via the runner.
#
# WiX Toolset v7.0.0 builds a Scope=perUser MSI rooted at
# PerUserProgramFilesFolder\FTMON. WiX v7 build tooling is licensed under the
# Open Source Maintainer Foundation Software License v1.1 (OSMF v1.1); the
# project owner accepts those terms for producing installers. FTMON source and
# redistributed FTMON binaries remain MIT. Third-party runtime notices are
# generated into THIRD_PARTY_NOTICES.txt at freeze time.
#
# The MSI never creates, starts, stops, or removes Scheduled Tasks and never
# runs `ftmon init`. Operators run Install-FTMONTasks.ps1 after init. The
# immutable ZIP artifact is the payload Chocolatey (#96) should consume later.
#
# Authenticode signing is optional until certificate / Azure Trusted Signing
# credentials are configured; unsigned prereleases are expected and documented.
#
# CI pins the exact CPython patch from versions.toml (not just "3.12"), runs
# tools/windows/check_version_agreement.py against the frozen ftmon.exe
# --version output, and exercises upgrade/downgrade/rollback via
# tools/windows/msi_lifecycle_test.py (synthetic MSIs sharing this UpgradeCode).

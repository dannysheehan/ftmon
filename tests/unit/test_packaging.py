"""Release asset contracts for M6 packaging (DO-02, TS-08)."""

from importlib.resources import files


def test_systemd_user_unit_is_packaged_do_02():
    """[TS-08] The documented user unit ships with the installed package."""
    unit = files("ftmon").joinpath("systemd/ftmon.service").read_text()
    assert "ExecStart=%h/.local/bin/ftmon daemon" in unit
    assert "WantedBy=default.target" in unit
    assert "User=root" not in unit


def test_hardened_server_unit_is_packaged_pm_09_do_06():
    """[PM-09][DO-06] The server unit fixes identity, paths, and write scope."""
    unit = files("ftmon").joinpath("systemd/ftmon-server.service").read_text()

    # Exact assertions make a future relaxation a reviewed security decision,
    # rather than an unnoticed consequence of refactoring service packaging.
    assert "User=ftmon" in unit
    assert "Group=ftmon" in unit
    assert "ExecStart=/usr/local/bin/ftmon daemon" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "PrivateTmp=yes" in unit
    assert "ConditionPathExists=/var/lib/ftmon/.config/ftmon/config.toml" in unit
    assert "UMask=0077" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=read-only" in unit
    assert "ReadWritePaths=/var/lib/ftmon /run/ftmon" in unit
    assert "CapabilityBoundingSet=\n" in unit
    assert "AmbientCapabilities=\n" in unit
    assert "SupplementaryGroups=\n" in unit
    assert "WantedBy=multi-user.target" in unit

    # The daemon must not be able to replace the program systemd will restart.
    assert "ExecStart=/var/lib/ftmon/" not in unit


def test_server_unit_preserves_process_visibility_pm_09():
    """[PM-09] Hardening must not make process-monitor results incomplete."""
    unit = files("ftmon").joinpath("systemd/ftmon-server.service").read_text()
    directives = {
        line.split("=", 1)[0]
        for line in unit.splitlines()
        if line and not line.lstrip().startswith("#") and "=" in line
    }

    # Comments document the deliberate omission, but only active directives
    # matter: ProtectProc=invisible would hide workloads owned by other users.
    assert "ProtectProc" not in directives


def test_server_unit_defines_non_session_filesystem_paths_pm_09():
    """[PM-09] A boot service must not rely on an interactive XDG environment."""
    unit = files("ftmon").joinpath("systemd/ftmon-server.service").read_text()

    assert "Environment=FTMON_CONFIG_DIR=/var/lib/ftmon/.config/ftmon" in unit
    assert "Environment=FTMON_DATA_DIR=/var/lib/ftmon/.local/share/ftmon" in unit
    assert "Environment=FTMON_STATE_DIR=/var/lib/ftmon/.local/state/ftmon" in unit
    assert "Environment=FTMON_RUNTIME_DIR=/run/ftmon" in unit
    assert "RuntimeDirectory=ftmon" in unit


def test_offline_web_brand_assets_are_packaged_ui_01():
    """[UI-01] Installed wheels retain every locally referenced brand variant."""
    brand = files("ftmon").joinpath("web/static/brand")
    assert brand.joinpath("ftmon-mark.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert brand.joinpath("favicon-64.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert brand.joinpath("apple-touch-icon.png").read_bytes().startswith(
        b"\x89PNG\r\n\x1a\n"
    )
    assert brand.joinpath("favicon.ico").read_bytes().startswith(b"\x00\x00\x01\x00")

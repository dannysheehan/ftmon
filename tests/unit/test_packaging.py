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


def test_versioned_demo_scenario_is_packaged_ui_16():
    """[UI-16] Deployment builds never depend on a source checkout fixture."""
    scenario = files("ftmon.scenarios").joinpath("demo-v1.jsonl").read_text()
    assert '"scenario":"demo-v1"' in scenario.splitlines()[0]


def test_public_demo_units_separate_build_read_and_refresh_ui_15_do_06():
    """[UI-15][DO-06] Packaged units preserve immutable deployment roles."""
    systemd = files("ftmon").joinpath("systemd")
    build = systemd.joinpath("ftmon-demo-build.service").read_text()
    web = systemd.joinpath("ftmon-demo-web.service").read_text()
    refresh = systemd.joinpath("ftmon-demo-refresh.service").read_text()
    timer = systemd.joinpath("ftmon-demo-refresh.timer").read_text()

    assert "User=ftmon-demo" in build
    assert "PrivateNetwork=yes" in build
    assert "ReadWritePaths=/var/lib/ftmon-demo" in build
    assert "/opt/ftmon-demo/bin/ftmon demo build --output " \
        "/var/lib/ftmon-demo/demo.db" in build
    assert "/usr/local/bin/ftmon" not in build
    assert "ReadWritePaths=" not in web
    assert "ReadOnlyPaths=/var/lib/ftmon-demo" in web
    assert "IPAddressDeny=any" in web and "IPAddressAllow=localhost" in web
    assert "--demo-host demo.ftmon.org" in web
    assert "ExecStart=/opt/ftmon-demo/bin/ftmon web --demo" in web
    assert "systemctl start ftmon-demo-build.service" in refresh
    assert "systemctl restart ftmon-demo-web.service" in refresh
    assert "try-restart" not in refresh
    assert "OnCalendar=*-*-* 03:17:00" in timer
    assert "Persistent=true" in timer


def test_demo_caddy_reference_has_real_rate_and_concurrency_caps_se_06():
    """[SE-06][DO-06] Proxy limits name their non-stock dependency explicitly."""
    caddy = files("ftmon").joinpath("deploy/Caddyfile.demo").read_text()

    # Rate limiting is a pinned custom module; concurrency uses documented
    # stock reverse-proxy controls, so neither boundary is merely commentary.
    assert "github.com/mholt/caddy-ratelimit@5625512f" in caddy
    assert "--output /tmp/caddy-ftmon-demo" in caddy
    assert "rate_limit {" in caddy
    assert "key {remote_host}" in caddy
    assert "unhealthy_request_count 32" in caddy
    assert "max_conns_per_host 32" in caddy
    assert "reverse_proxy 127.0.0.1:8420" in caddy

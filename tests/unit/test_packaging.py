"""Release asset contracts for M6 packaging (DO-02, TS-08)."""

from importlib.resources import files


def test_systemd_user_unit_is_packaged_do_02():
    """[TS-08] The documented user unit ships with the installed package."""
    unit = files("ftmon").joinpath("systemd/ftmon.service").read_text()
    assert "ExecStart=%h/.local/bin/ftmon daemon" in unit
    assert "WantedBy=default.target" in unit
    assert "User=root" not in unit


def test_offline_web_brand_assets_are_packaged_ui_01():
    """[UI-01] Installed wheels retain every locally referenced brand variant."""
    brand = files("ftmon").joinpath("web/static/brand")
    assert brand.joinpath("ftmon-mark.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert brand.joinpath("favicon-64.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert brand.joinpath("apple-touch-icon.png").read_bytes().startswith(
        b"\x89PNG\r\n\x1a\n"
    )
    assert brand.joinpath("favicon.ico").read_bytes().startswith(b"\x00\x00\x01\x00")

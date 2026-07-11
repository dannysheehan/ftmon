"""Release asset contracts for M6 packaging (DO-02, TS-08)."""

from importlib.resources import files


def test_systemd_user_unit_is_packaged_do_02():
    """[TS-08] The documented user unit ships with the installed package."""
    unit = files("ftmon").joinpath("systemd/ftmon.service").read_text()
    assert "ExecStart=%h/.local/bin/ftmon daemon" in unit
    assert "WantedBy=default.target" in unit
    assert "User=root" not in unit

"""[PM-08][NO-10][SE-05] Channel config and secret-reference safety."""

from __future__ import annotations

import pytest

from ftmon.config import SecretRef, load_config


def test_secret_value_is_redacted_and_env_resolution_is_explicit():
    value = SecretRef(env="TOKEN").resolve({"TOKEN": "keep-me-secret"})
    assert str(value) == repr(value) == "<redacted>"
    assert value._reveal() == "keep-me-secret"
    with pytest.raises(ValueError, match="not set"):
        SecretRef(env="TOKEN").resolve({})


def test_secret_file_requires_private_regular_owned_file(tmp_path):
    secret = tmp_path / "token"
    secret.write_text("  token-value\n")
    secret.chmod(0o600)
    assert SecretRef(file=secret).resolve()._reveal() == "token-value"

    secret.chmod(0o640)
    with pytest.raises(ValueError, match="group/world"):
        SecretRef(file=secret).resolve()

    secret.chmod(0o600)
    secret.write_text("x" * 8193)
    with pytest.raises(ValueError, match="8 KiB"):
        SecretRef(file=secret).resolve()


def test_secret_file_rejects_symlink_and_embedded_controls(tmp_path):
    target = tmp_path / "target"
    target.write_text("secret")
    target.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="opened safely"):
        SecretRef(file=link).resolve()

    target.write_text("one\ntwo")
    with pytest.raises(ValueError, match="embedded newline"):
        SecretRef(file=target).resolve()


def test_literal_and_ambiguous_secret_references_disable_only_bad_channel(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        "[notify.desktop]\nenabled = true\n"
        "[notify.ntfy]\nenabled = true\ntopic = 'host'\ntoken = 'literal'\n"
        "token_env = 'TOKEN'\ntoken_file = '/tmp/token'\n"
    )
    cfg, warnings = load_config(config)
    assert cfg.channel("desktop").enabled
    assert not cfg.channel("ntfy").enabled
    rendered = "\n".join(warnings)
    assert "literal token is forbidden" in rendered
    assert "mutually exclusive" in rendered
    assert "literal" not in rendered.replace("literal token", "")


def test_valid_enabled_channels_validate_references_without_retaining_values(
    tmp_path, monkeypatch
):
    """[NO-10] Loading checks credentials but retains only safe references."""
    config = tmp_path / "config.toml"
    monkeypatch.setenv("MISSING_NOW", "ntfy-token")
    monkeypatch.setenv("WEBHOOK_URL", "https://hooks.example/path?token=secret")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-password")
    config.write_text(
        "[notify.ntfy]\nenabled=true\ntopic='host'\ntoken_env='MISSING_NOW'\n"
        "[notify.webhook]\nenabled=true\nurl_env='WEBHOOK_URL'\n"
        "[notify.smtp]\nenabled=true\nhost='mail.example'\nport=465\n"
        "tls='implicit'\nto=['ops@example.net']\npassword_env='SMTP_PASSWORD'\n"
    )
    cfg, warnings = load_config(config)
    assert warnings == []
    assert all(cfg.channel(name).enabled for name in ("ntfy", "webhook", "smtp"))
    assert cfg.channel("ntfy").secret.env == "MISSING_NOW"


def test_enabled_channel_with_missing_secret_fails_closed(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        "[notify.ntfy]\nenabled=true\ntopic='host'\ntoken_env='ABSENT_TOKEN'\n"
    )
    cfg, warnings = load_config(config)
    assert not cfg.channel("ntfy").enabled
    assert warnings == [
        "[notify.ntfy] secret reference is unavailable or unsafe; channel disabled"
    ]


def test_secret_ref_requires_exactly_one_source(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        SecretRef()
    with pytest.raises(ValueError, match="exactly one"):
        SecretRef(env="TOKEN", file=tmp_path / "token")

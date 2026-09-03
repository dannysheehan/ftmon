"""[DO-02] Third-party notices generation for the Windows freeze."""

from __future__ import annotations

from pathlib import Path

from tools.windows import gen_notices


def test_license_text_finds_licenses_subdirectory():
    """Licence files under dist-info/licenses/ must be discovered."""
    import importlib.metadata as md

    dist = md.distribution("httpx2")
    text = gen_notices._license_text(dist)
    assert text is not None
    assert len(text.strip()) > 20


def test_collect_notices_unions_onedir_with_build_env(tmp_path: Path):
    """A thin onedir *.dist-info scan must not drop the runtime closure."""
    # Minimal fake dist-info that would previously short-circuit the closure.
    info = tmp_path / "onlypkg-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: onlypkg\nVersion: 1.0\n",
        encoding="utf-8",
    )
    notices = gen_notices._collect_notices(onedir=tmp_path)
    assert "onlypkg" in notices
    # Build-env closure packages remain present.
    keys = " ".join(notices).lower().replace("_", "-")
    for required in ("pydantic", "anyio", "httpx2", "click", "markupsafe"):
        assert required in keys
    assert len(notices) >= 20
    # Prefer build-env licence text over empty onedir metadata.
    pydantic_notice = next(v for k, v in notices.items() if "pydantic" == k)
    assert "(no LICENSE file" not in pydantic_notice

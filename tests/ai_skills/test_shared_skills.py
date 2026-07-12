"""Portable shared-skill structure and safety contract (TS-20)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
SKILLS = ROOT / ".ai" / "skills"
NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")


def _frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    header, body = text[4:].split("\n---\n", 1)
    fields: dict[str, str] = {}
    for line in header.splitlines():
        key, separator, value = line.partition(":")
        assert separator and key and value.strip()
        fields[key] = value.strip()
    return fields, body


def test_shared_skills_have_portable_bounded_metadata():
    """[AS-01][AS-05][TS-20] Discovery metadata stays portable and concise."""
    skill_dirs = sorted(path for path in SKILLS.iterdir() if path.is_dir())
    assert [path.name for path in skill_dirs] == ["ftmon-add-extra-monitor"]
    for directory in skill_dirs:
        fields, body = _frontmatter(directory / "SKILL.md")
        assert set(fields) == {"name", "description"}
        assert fields["name"] == directory.name
        assert NAME_RE.fullmatch(fields["name"])
        assert 20 <= len(fields["description"]) <= 1024
        assert len(body.splitlines()) < 500
        assert "TODO" not in body


def test_extra_monitor_skill_covers_both_protocols_and_repository_authority():
    """[AS-01][AS-02][TS-20] The shared workflow starts from live authority."""
    text = (SKILLS / "ftmon-add-extra-monitor/SKILL.md").read_text()
    for required in (
        "AGENTS.md",
        "SPEC.md",
        "DESIGN.md",
        "docs/external-checks.md",
        "extra-monitors/README.md",
        "extra-monitors/_template/",
        "tests/extra_monitors/test_recipes.py",
        "nagios",
        "ftmon-json",
        "real-system-verified",
        "recipe-only",
        "FTMON JSON",
    ):
        assert required in text
    for path in (
        ROOT / "AGENTS.md",
        ROOT / "SPEC.md",
        ROOT / "DESIGN.md",
        ROOT / "docs/external-checks.md",
        ROOT / "extra-monitors/README.md",
        ROOT / "extra-monitors/_template",
        ROOT / "tests/extra_monitors/test_recipes.py",
    ):
        assert path.exists()


def test_extra_monitor_skill_preserves_security_evidence_and_user_authority():
    """[AS-02][AS-03][TS-20] High-risk boundaries cannot disappear unnoticed."""
    text = (SKILLS / "ftmon-add-extra-monitor/SKILL.md").read_text().lower()
    for concept in (
        "never fabricate",
        "first line",
        "finite labels",
        "licence",
        "passwords",
        "sudo -n",
        "root-owned wrapper",
        "never grant broad",
        "enabled = false",
        "do not push",
        "explicitly authorizes",
        "preserve unrelated user changes",
        "do not loosen",
    ):
        assert concept in text


def test_extra_monitor_skill_names_artifacts_exchange_and_validation_gates():
    """[AS-02][AS-05][TS-20] A recipe is not done before publication checks."""
    text = (SKILLS / "ftmon-add-extra-monitor/SKILL.md").read_text()
    for artifact in (
        "checks.toml.example",
        "monitor.toml",
        "README",
        "fixtures",
        "recipe",
        "[[trend]]",
        "tools/build_exchange.py",
        "tests/exchange",
        "tests/extra_monitors",
        "uv run pytest -q",
        "uv run ruff check",
        "git diff --check",
    ):
        assert artifact in text


def test_vendor_metadata_and_installation_docs_point_to_canonical_skill():
    """[AS-04][AS-05][TS-20] Vendor adapters cannot become a second workflow."""
    directory = SKILLS / "ftmon-add-extra-monitor"
    metadata = (directory / "agents/openai.yaml").read_text()
    assert "Add FTMON Extra Monitor" in metadata
    assert "$ftmon-add-extra-monitor" in metadata
    assert not any(path.is_symlink() for path in directory.rglob("*"))

    docs = (ROOT / "docs/ai-skills.md").read_text()
    assert ".ai/skills/ftmon-add-extra-monitor/SKILL.md" in docs
    assert "${CODEX_HOME:-$HOME/.codex}/skills" in docs
    assert "~/.claude/skills/" in docs
    assert ".claude/skills/" in docs
    assert "audit a skill like executable code" in docs.lower()

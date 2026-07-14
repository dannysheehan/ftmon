"""Static Exchange generation and publication boundaries (TS-19)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from build_exchange import BuildError, build  # noqa: E402


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _catalogue(tmp_path: Path) -> Path:
    destination = tmp_path / "catalogue"
    shutil.copytree(ROOT / "extra-monitors", destination)
    return destination


def test_exchange_build_is_deterministic_complete_and_no_js_optional(tmp_path):
    """[XR-06][XR-07][TS-19] One authority produces stable browseable artifacts."""
    first, second = tmp_path / "first", tmp_path / "second"
    build(first)
    build(second)
    assert _files(first) == _files(second)

    index = (first / "index.html").read_text()
    detail = (first / "recipes/http-tls/index.html").read_text()
    search = json.loads((first / "search-index.v1.json").read_text())
    assert 'href="recipes/http-tls/"' in index
    assert "data-recipe-id=\"http-tls\"" in index
    assert "<noscript>" in index
    assert "Monitoring Plugins 2.3.5" in detail
    assert "/usr/lib/nagios/plugins/check_http" in detail
    assert search["schema"] == 1
    assert [recipe["id"] for recipe in search["recipes"]] == [
        "http-tls", "root-disk", "temperature",
    ]
    temperature = (first / "recipes/temperature/index.html").read_text()
    assert "check_temperature" in temperature
    assert (first / "CNAME").read_text() == "exchange.ftmon.org\n"


def test_exchange_escapes_active_content_and_never_executes_recipe_files(tmp_path):
    """[XR-08][XR-10][TS-19] Publication keeps prose and commands inert."""
    catalogue = _catalogue(tmp_path)
    recipe = catalogue / "http-tls"
    readme = recipe / "README.md"
    readme.write_text(readme.read_text() + "\n<script>alert('unsafe')</script>\n")
    marker = tmp_path / "executed"
    script = recipe / "scripts/payload.sh"
    script.parent.mkdir()
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)

    output = tmp_path / "site"
    build(output, catalogue)
    detail = (output / "recipes/http-tls/index.html").read_text()
    assert "<script>alert" not in detail
    assert "&lt;script&gt;alert" in detail
    assert not marker.exists()


def test_exchange_rejects_unsafe_links_symlinks_and_unmarked_replacement(tmp_path):
    """[XR-08][TS-19] Unsafe input and ambiguous destinations fail closed."""
    catalogue = _catalogue(tmp_path)
    readme = catalogue / "http-tls/README.md"
    readme.write_text(readme.read_text() + "\n[bad](javascript:alert(1))\n")
    with pytest.raises(BuildError, match="unsafe Markdown link"):
        build(tmp_path / "bad-link", catalogue)

    catalogue = _catalogue(tmp_path / "symlink-case")
    os.symlink("README.md", catalogue / "http-tls/linked-readme")
    with pytest.raises(BuildError, match="symlink"):
        build(tmp_path / "bad-symlink", catalogue)

    unmarked = tmp_path / "not-generated"
    unmarked.mkdir()
    (unmarked / "valuable.txt").write_text("keep")
    with pytest.raises(BuildError, match="unmarked"):
        build(unmarked)
    assert (unmarked / "valuable.txt").read_text() == "keep"


def test_exchange_generated_local_links_resolve(tmp_path):
    """[XR-07][TS-19] Every generated local navigation and asset target exists."""
    output = tmp_path / "site"
    build(output)
    attributes = re.compile(r'(?:href|src)="([^"]+)"')
    for page in output.rglob("*.html"):
        for target in attributes.findall(page.read_text()):
            parsed = urlsplit(target)
            if parsed.scheme in {"http", "https"} or target.startswith("#"):
                continue
            candidate = (
                output / parsed.path.lstrip("/")
                if parsed.path.startswith("/")
                else page.parent / parsed.path
            )
            assert candidate.exists(), f"{page.relative_to(output)} -> {target}"


def test_exchange_workflow_builds_prs_but_deploys_only_main_pushes():
    """[XR-09][TS-19] Contributor builds cannot acquire Pages deployment authority."""
    workflow = (ROOT / ".github/workflows/exchange.yml").read_text()
    assert "pull_request:" in workflow
    assert "contents: read" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow
    guard = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    assert workflow.count(guard) >= 3
    uses = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", workflow)
    assert uses and all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in uses)

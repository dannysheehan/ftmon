"""Generic offline contract for the curated extra-monitor catalogue."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from ftmon.checks.jsoncheck import parse as parse_json
from ftmon.checks.nagios import parse as parse_nagios
from ftmon.definitions.loader import load_file

ROOT = Path(__file__).parents[2]
CATALOGUE = ROOT / "extra-monitors"
RECIPE_KEYS = {
    "id", "title", "summary", "kind", "platforms", "upstream", "license",
    "status", "privilege", "network", "last_verified_version",
    "category", "tags", "min_ftmon_version",
}
FIXTURE_KEYS = {"path", "exit_code", "state", "labels"}
DOC_HEADINGS = {
    "## Why", "## Install", "## Configure", "## Test",
    "## Security and permissions", "## Upstream and licence",
}


def recipe_dirs() -> list[Path]:
    return sorted(
        path for path in CATALOGUE.iterdir()
        if path.is_dir() and not path.name.startswith("_")
    )


def test_catalogue_template_explains_every_required_artifact():
    """[DO-08] Contributors start from the same documented, testable shape."""
    template = CATALOGUE / "_template"
    assert {
        "README.md", "recipe.toml", "checks.toml.example", "monitor.toml", "fixtures",
    } <= {item.name for item in template.iterdir()}
    assert DOC_HEADINGS <= set((template / "README.md").read_text().splitlines())


@pytest.fixture(params=recipe_dirs(), ids=lambda path: path.name)
def recipe(request) -> tuple[Path, dict]:
    path = request.param
    with (path / "recipe.toml").open("rb") as stream:
        return path, tomllib.load(stream)


def test_recipe_metadata_and_documentation_contract(recipe):
    """[XR-01] Every article has bounded, searchable compatibility metadata."""
    path, manifest = recipe
    assert set(manifest) == {"schema", "recipe", "fixtures"}
    assert manifest["schema"] == 1
    meta = manifest["recipe"]
    assert set(meta) == RECIPE_KEYS
    assert meta["id"] == path.name
    assert meta["kind"] in {"nagios", "ftmon-json"}
    assert meta["status"] in {"tested", "real-system-verified", "recipe-only"}
    assert meta["privilege"] in {"none", "sudo-wrapper"}
    assert meta["platforms"] and set(meta["platforms"]) <= {"linux", "darwin", "windows"}
    assert meta["upstream"].startswith("https://")
    assert meta["license"].strip()
    assert type(meta["network"]) is bool
    assert meta["category"] in {
        "applications", "database", "hardware", "network", "other", "security",
        "storage", "system", "web",
    }
    assert meta["tags"] == sorted(set(meta["tags"]))
    assert all(tag and tag == tag.lower() for tag in meta["tags"])
    assert meta["min_ftmon_version"]

    readme = (path / "README.md").read_text()
    assert DOC_HEADINGS <= set(readme.splitlines())
    assert meta["upstream"] in readme
    assert meta["license"] in readme


def test_recipe_registry_and_monitor_agree_without_granting_authority(recipe):
    """[XR-02][SE-07] Definitions select an alias; only registry examples hold argv."""
    path, manifest = recipe
    with (path / "checks.toml.example").open("rb") as stream:
        registry = tomllib.load(stream)
    assert set(registry) == {"check"}
    assert len(registry["check"]) == 1
    alias, entry = next(iter(registry["check"].items()))
    assert set(entry) == {"argv", "protocol", "timeout"}
    assert entry["protocol"] == manifest["recipe"]["kind"]
    assert entry["argv"] and Path(entry["argv"][0]).is_absolute()
    assert all(isinstance(argument, str) and argument for argument in entry["argv"])
    if manifest["recipe"]["privilege"] == "sudo-wrapper":
        assert entry["argv"][:2] == ["/usr/bin/sudo", "-n"]

    definition_text = (path / "monitor.toml").read_text()
    definition = load_file(path / "monitor.toml")
    assert definition.source == "external"
    assert definition.source_options["check"] == alias
    assert "argv" not in definition_text


def test_recipe_fixtures_match_documented_protocol_and_metrics(recipe):
    """[XR-03][TS-16] Offline fixtures prove state and perfdata compatibility."""
    path, manifest = recipe
    kind = manifest["recipe"]["kind"]
    fixtures = manifest["fixtures"]
    assert fixtures
    seen_states = set()
    for fixture in fixtures:
        assert set(fixture) == FIXTURE_KEYS
        output_path = path / fixture["path"]
        assert output_path.is_file() and output_path.is_relative_to(path / "fixtures")
        output = output_path.read_bytes()
        result = (
            parse_nagios(output, fixture["exit_code"], 0)
            if kind == "nagios" else parse_json(output, 0)
        )
        assert result.state == fixture["state"]
        assert set(result.values) == set(fixture["labels"])
        seen_states.add(result.state)
    assert 0 in seen_states


def test_recipe_contains_no_vendored_plugin_or_obvious_secret(recipe):
    """[XR-04][EC-09] Recipes document dependencies without copying or credentialing them."""
    path, _manifest = recipe
    allowed = {
        "README.md", "recipe.toml", "checks.toml.example", "monitor.toml", "fixtures",
        "scripts", "tests",
    }
    assert {item.name for item in path.iterdir()} <= allowed
    combined = "\n".join(
        file.read_text(errors="replace")
        for file in path.rglob("*") if file.is_file()
    ).lower()
    assert "password=" not in combined
    assert "token=" not in combined

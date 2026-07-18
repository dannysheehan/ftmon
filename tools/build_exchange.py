#!/usr/bin/env python3
"""Build the inert static FTMON Exchange catalogue (XR-06..10).

The publisher intentionally treats recipes as untrusted data. It reads and
escapes their bounded files but never imports scripts or invokes check commands.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "extra-monitors"
EXCHANGE = ROOT / "exchange"
SOURCE_BASE = "https://github.com/dannysheehan/ftmon/tree/main/extra-monitors"
MARKER = ".ftmon-exchange-output"
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:a|b|rc)?[0-9]*$")
CATEGORIES = {
    "applications",
    "database",
    "hardware",
    "network",
    "other",
    "security",
    "storage",
    "system",
    "web",
}
PRIVILEGE_CLASSES = {"none", "service-socket", "sudo-wrapper"}
META_KEYS = {
    "id",
    "title",
    "summary",
    "kind",
    "platforms",
    "upstream",
    "license",
    "status",
    "privilege",
    "network",
    "last_verified_version",
    "category",
    "tags",
    "min_ftmon_version",
}
SAFE_LINK = re.compile(r"^https://[^\s<>]+$")


class BuildError(ValueError):
    """A recipe cannot be published safely or deterministically."""


@dataclass(frozen=True)
class Recipe:
    """Validated publication fields; commands remain inert text."""

    meta: dict
    readme: str
    checks: str
    monitor: str


def _regular_text(path: Path, recipe_root: Path, limit: int = 128 * 1024) -> str:
    if path.is_symlink() or not path.is_file() or not path.is_relative_to(recipe_root):
        raise BuildError(f"unsafe recipe file: {path.name}")
    data = path.read_bytes()
    if len(data) > limit:
        raise BuildError(f"recipe file too large: {path.name}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError(f"recipe file is not UTF-8: {path.name}") from exc


def _assert_no_symlinks(path: Path) -> None:
    for item in path.rglob("*"):
        if item.is_symlink():
            raise BuildError(f"recipe contains symlink: {item.relative_to(path)}")


def _validate_meta(meta: dict, directory: Path) -> dict:
    if set(meta) != META_KEYS:
        raise BuildError(f"{directory.name}: metadata keys do not match schema")
    rid = meta["id"]
    if not isinstance(rid, str) or not ID_RE.fullmatch(rid) or rid != directory.name:
        raise BuildError(f"{directory.name}: invalid recipe id")
    for key in ("title", "summary", "license", "last_verified_version"):
        value = meta[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 240:
            raise BuildError(f"{rid}: invalid {key}")
    if meta["kind"] not in {"nagios", "ftmon-json"}:
        raise BuildError(f"{rid}: invalid protocol")
    if meta["status"] not in {"tested", "real-system-verified", "recipe-only"}:
        raise BuildError(f"{rid}: invalid confidence status")
    if meta["privilege"] not in PRIVILEGE_CLASSES:
        raise BuildError(f"{rid}: invalid privilege")
    if type(meta["network"]) is not bool:
        raise BuildError(f"{rid}: invalid network flag")
    platforms = meta["platforms"]
    if (
        not isinstance(platforms, list)
        or not platforms
        or len(platforms) > 3
        or not set(platforms) <= {"linux", "darwin", "windows"}
    ):
        raise BuildError(f"{rid}: invalid platforms")
    if meta["category"] not in CATEGORIES:
        raise BuildError(f"{rid}: invalid category")
    tags = meta["tags"]
    if (
        not isinstance(tags, list)
        or len(tags) > 12
        or tags != sorted(set(tags))
        or not all(isinstance(tag, str) and TAG_RE.fullmatch(tag) for tag in tags)
    ):
        raise BuildError(f"{rid}: invalid tags")
    if not isinstance(meta["min_ftmon_version"], str) or not VERSION_RE.fullmatch(
        meta["min_ftmon_version"]
    ):
        raise BuildError(f"{rid}: invalid minimum FTMON version")
    parsed = urlparse(meta["upstream"])
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise BuildError(f"{rid}: unsafe upstream URL")
    return dict(meta)


def load_recipes(catalogue: Path = CATALOGUE) -> list[Recipe]:
    """Load bounded recipe data without executing any catalogue content."""
    recipes: list[Recipe] = []
    seen: set[str] = set()
    for directory in sorted(catalogue.iterdir(), key=lambda path: path.name):
        if not directory.is_dir() or directory.name.startswith("_"):
            continue
        if directory.is_symlink():
            raise BuildError(f"recipe directory is a symlink: {directory.name}")
        _assert_no_symlinks(directory)
        manifest_text = _regular_text(directory / "recipe.toml", directory)
        try:
            manifest = tomllib.loads(manifest_text)
        except tomllib.TOMLDecodeError as exc:
            raise BuildError(f"{directory.name}: invalid recipe TOML") from exc
        if set(manifest) != {"schema", "recipe", "fixtures"} or manifest["schema"] != 1:
            raise BuildError(f"{directory.name}: unsupported recipe schema")
        meta = _validate_meta(manifest["recipe"], directory)
        if meta["id"] in seen:
            raise BuildError(f"duplicate recipe id: {meta['id']}")
        seen.add(meta["id"])
        meta["source_url"] = f"{SOURCE_BASE}/{meta['id']}"
        recipes.append(
            Recipe(
                meta=meta,
                readme=_regular_text(directory / "README.md", directory),
                checks=_regular_text(directory / "checks.toml.example", directory),
                monitor=_regular_text(directory / "monitor.toml", directory),
            )
        )
    return recipes


def _inline(text: str) -> str:
    """Escape text, then add only inert inline code and HTTPS links."""
    tokens: list[str] = []

    def stash(value: str) -> str:
        tokens.append(value)
        return f"\x00{len(tokens) - 1}\x00"

    text = re.sub(r"`([^`\n]+)`", lambda m: stash(f"<code>{html.escape(m.group(1))}</code>"), text)

    def link(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if not SAFE_LINK.fullmatch(target):
            raise BuildError(f"unsafe Markdown link: {target}")
        return stash(f'<a href="{html.escape(target, quote=True)}">{html.escape(label)}</a>')

    text = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", link, text)
    text = re.sub(r"<((?:https://)[^<>\s]+)>", lambda m: link(_LinkMatch(m.group(1))), text)
    escaped = html.escape(text)
    for index, token in enumerate(tokens):
        escaped = escaped.replace(html.escape(f"\x00{index}\x00"), token)
    return escaped


class _LinkMatch:
    """Adapt an autolink to the explicit-link callback without another parser."""

    def __init__(self, target: str):
        self.target = target

    def group(self, number: int) -> str:
        return self.target


def render_markdown(text: str) -> str:
    """Render the documented safe subset; raw HTML remains escaped text."""
    output: list[str] = []
    paragraph: list[str] = []
    in_code = False
    code: list[str] = []
    list_open = False

    def flush_paragraph() -> None:
        if paragraph:
            output.append(f"<p>{_inline(' '.join(part.strip() for part in paragraph))}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            output.append("</ul>")
            list_open = False

    for line in text.splitlines():
        if line.startswith("```"):
            flush_paragraph()
            close_list()
            if in_code:
                output.append(f"<pre><code>{html.escape(chr(10).join(code))}</code></pre>")
                code.clear()
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue
        heading = re.fullmatch(r"(#{1,3})\s+(.+)", line)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            # Recipe title duplicates the page h1; demote it while preserving structure.
            if level == 1:
                continue
            output.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue
        item = re.fullmatch(r"-\s+(.+)", line)
        if item:
            flush_paragraph()
            if not list_open:
                output.append("<ul>")
                list_open = True
            output.append(f"<li>{_inline(item.group(1))}</li>")
            continue
        if not line.strip():
            flush_paragraph()
            close_list()
        else:
            paragraph.append(line)
    if in_code:
        raise BuildError("unclosed Markdown code fence")
    flush_paragraph()
    close_list()
    return "\n".join(output)


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(EXCHANGE / "templates"),
        autoescape=select_autoescape(("html",)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def build(output: Path, catalogue: Path = CATALOGUE) -> None:
    """Build into a fresh tree and replace only a prior marked build."""
    output = output.resolve()
    catalogue = catalogue.resolve()
    if output == catalogue or output.is_relative_to(catalogue) or catalogue.is_relative_to(output):
        raise BuildError("output must be separate from recipe authority")
    recipes = load_recipes(catalogue)
    env = _environment()
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ftmon-exchange-", dir=parent) as temporary:
        stage = Path(temporary)
        meta = [recipe.meta for recipe in recipes]
        _write(
            stage / "index.html",
            env.get_template("index.html").render(
                recipes=meta,
                categories=sorted({item["category"] for item in meta}),
                platforms=sorted({value for item in meta for value in item["platforms"]}),
                protocols=sorted({item["kind"] for item in meta}),
                privileges=sorted({item["privilege"] for item in meta}),
            ),
        )
        for recipe in recipes:
            _write(
                stage / "recipes" / recipe.meta["id"] / "index.html",
                env.get_template("detail.html").render(
                    recipe=recipe.meta,
                    article_html=render_markdown(recipe.readme),
                    checks_text=recipe.checks,
                    monitor_text=recipe.monitor,
                ),
            )
        search = {
            "schema": 1,
            "recipes": [
                {
                    **item,
                    "search_text": " ".join(
                        [
                            item["id"],
                            item["title"],
                            item["summary"],
                            item["category"],
                            *item["tags"],
                        ]
                    ).lower(),
                }
                for item in meta
            ],
        }
        _write(
            stage / "search-index.v1.json",
            json.dumps(search, sort_keys=True, separators=(",", ":")) + "\n",
        )
        shutil.copytree(EXCHANGE / "static", stage / "assets")
        shutil.copy2(EXCHANGE / "templates" / "404.html", stage / "404.html")
        _write(stage / "CNAME", "exchange.ftmon.org\n")
        _write(stage / ".nojekyll", "")
        _write(stage / MARKER, "generated by tools/build_exchange.py\n")
        if output.exists():
            if not (output / MARKER).is_file():
                raise BuildError("refusing to replace an unmarked output directory")
            shutil.rmtree(output)
        stage.rename(output)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the static FTMON Exchange")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "exchange")
    args = parser.parse_args()
    try:
        build(args.output)
    except (BuildError, OSError) as exc:
        print(f"exchange build failed: {exc}", file=sys.stderr)
        return 1
    print(f"built FTMON Exchange at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

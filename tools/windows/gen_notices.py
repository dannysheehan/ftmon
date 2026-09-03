"""Deterministic third-party notices for the Windows frozen payload."""

from __future__ import annotations

import argparse
import importlib.metadata
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Direct runtime dependencies plus commonly bundled transitive packages.
# Notices always include this set plus the installed ``ftmon`` requirement
# closure from the build env, unioned with any *.dist-info retained in a
# frozen onedir (onedir alone is incomplete under PyInstaller).
_FALLBACK_RUNTIME = (
    "anyio",
    "click",
    "h11",
    "httpcore2",
    "httpx2",
    "idna",
    "jinja2",
    "jsonschema",
    "jsonschema-specifications",
    "markupsafe",
    "mcp",
    "mcp-types",
    "platformdirs",
    "psutil",
    "pydantic",
    "pydantic-core",
    "pywin32",
    "referencing",
    "rpds-py",
    "starlette",
    "tomli-w",
    "typing-extensions",
    "uvicorn",
    "windows-toasts",
)

_REQ_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._+-]*)")

# Paths relative to the distribution / .dist-info root (PEP 639 layout first).
_LICENSE_CANDIDATES = (
    "licenses/LICENSE",
    "licenses/LICENSE.txt",
    "licenses/LICENSE.md",
    "licenses/COPYING",
    "licenses/NOTICE",
    "license_files/LICENSE",
    "LICENSE",
    "LICENSE.txt",
    "LICENSE.md",
    "COPYING",
    "NOTICE",
    "LICENCE",
    "LICENCE.txt",
)


def _norm_name(name: str) -> str:
    return name.lower().replace("_", "-")


def _license_text(dist: importlib.metadata.Distribution) -> str | None:
    """Locate licence text via known paths, then ``dist.files`` (incl. licenses/)."""
    for candidate in _LICENSE_CANDIDATES:
        try:
            text = dist.read_text(candidate)
        except Exception:
            text = None
        if text and text.strip():
            return text

    for file in dist.files or ():
        rel = str(file).replace("\\", "/")
        leaf = rel.rsplit("/", 1)[-1].lower()
        if leaf.endswith((".py", ".pyc", ".pyi", ".typed")):
            continue
        if not any(
            key in leaf for key in ("license", "licence", "copying", "notice")
        ):
            continue
        try:
            located = file.locate()
        except Exception:
            located = None
        if located is not None and Path(located).is_file():
            try:
                text = Path(located).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if text.strip():
                return text
        # Fall back to metadata-relative read (PathDistribution / wheels).
        for attempt in (rel, rel.split(".dist-info/", 1)[-1]):
            try:
                text = dist.read_text(attempt)
            except Exception:
                text = None
            if text and text.strip():
                return text
    return None


def _notice_for_dist(dist: importlib.metadata.Distribution) -> str:
    version = dist.version
    name = dist.metadata["Name"]
    license_text = _license_text(dist)
    meta_license = (
        dist.metadata.get("License")
        or dist.metadata.get("License-Expression")
        or "UNKNOWN"
    )
    header = f"## {name} {version}\n\nMetadata-License: {meta_license}\n\n"
    if license_text:
        return header + license_text.strip() + "\n"
    return header + "(no LICENSE file in distribution metadata)\n"


def _notice_for_name(dist_name: str) -> str | None:
    try:
        dist = importlib.metadata.distribution(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return None
    return _notice_for_dist(dist)


def _dists_from_onedir(onedir: Path) -> list[importlib.metadata.Distribution]:
    """Collect .dist-info directories shipped beside the frozen executable."""
    found: list[importlib.metadata.Distribution] = []
    for info in sorted(onedir.rglob("*.dist-info")):
        if not info.is_dir():
            continue
        try:
            found.append(importlib.metadata.PathDistribution(info))
        except Exception:
            continue
    found.sort(key=lambda d: (d.metadata["Name"] or "").lower())
    return found


def _requirement_name(req: str) -> str | None:
    match = _REQ_NAME.match(req.strip())
    return match.group(1) if match else None


def _build_env_runtime_names() -> list[str]:
    """Names to document: explicit fallback set plus ``ftmon`` requires closure."""
    names: set[str] = {_norm_name(n) for n in _FALLBACK_RUNTIME}
    stack = ["ftmon", *_FALLBACK_RUNTIME]
    seen: set[str] = set()
    while stack:
        raw = stack.pop()
        key = _norm_name(raw)
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = importlib.metadata.distribution(raw)
        except importlib.metadata.PackageNotFoundError:
            continue
        names.add(_norm_name(dist.metadata["Name"] or raw))
        for req in dist.requires or ():
            if "extra ==" in req:
                continue
            child = _requirement_name(req)
            if child:
                stack.append(child)
    ordered: list[str] = []
    for key in sorted(names):
        try:
            dist = importlib.metadata.distribution(key)
        except importlib.metadata.PackageNotFoundError:
            ordered.append(key)
            continue
        ordered.append(dist.metadata["Name"] or key)
    return ordered


def _collect_notices(*, onedir: Path | None) -> dict[str, str]:
    """Union build-env closure with optional frozen *.dist-info (keyed by name).

    Build-env entries win when both sides have the same package so licence
    files under site-packages (``licenses/LICENSE``) are preferred over thin
    onedir metadata that often omits them.
    """
    notices: dict[str, str] = {}
    for name in _build_env_runtime_names():
        text = _notice_for_name(name)
        key = _norm_name(name)
        if text is None:
            notices[key] = f"## {name}\n\n(not installed in the build environment)\n"
        else:
            notices[key] = text

    if onedir is not None:
        for dist in _dists_from_onedir(onedir):
            key = _norm_name(dist.metadata["Name"] or "unknown")
            if key in notices:
                # Keep build-env notice unless it lacked a licence file and the
                # onedir copy has one.
                if "(no LICENSE file" not in notices[key]:
                    continue
                candidate = _notice_for_dist(dist)
                if "(no LICENSE file" not in candidate:
                    notices[key] = candidate
                continue
            notices[key] = _notice_for_dist(dist)
    return notices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-onedir",
        type=Path,
        default=None,
        help="also scan a frozen onedir for *.dist-info (unioned with build-env)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "packaging" / "windows" / "THIRD_PARTY_NOTICES.txt",
    )
    args = parser.parse_args(argv)

    parts = [
        "THIRD_PARTY_NOTICES for the FTMON Windows frozen distribution\n",
        "Generated deterministically at freeze time from the build environment,\n",
        "unioned with any *.dist-info retained in the frozen onedir.\n",
        "FTMON itself is MIT-licensed; see LICENSE beside ftmon.exe.\n",
    ]

    notices = _collect_notices(onedir=args.from_onedir)
    ordered = [notices[k] for k in sorted(notices)]

    joined = "\n".join(ordered).lower().replace("_", "-")
    for required in (
        "pydantic",
        "pydantic-core",
        "markupsafe",
        "jsonschema",
        "click",
        "anyio",
        "httpx2",
        "httpcore2",
    ):
        if required not in joined:
            raise SystemExit(f"notices missing expected package {required!r}")

    missing_license = sum(1 for n in ordered if "(no LICENSE file" in n)
    if missing_license > len(ordered) // 2:
        raise SystemExit(
            f"too many notices lack licence text ({missing_license}/{len(ordered)}); "
            "check licenses/ discovery"
        )

    for notice in ordered:
        parts.append("\n" + ("-" * 72) + "\n")
        parts.append(notice)

    body = "".join(parts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(body, encoding="utf-8")
    if args.from_onedir is not None:
        beside = args.from_onedir / "THIRD_PARTY_NOTICES.txt"
        beside.write_text(body, encoding="utf-8")
        print(f"wrote {beside}")
    print(
        f"wrote {args.output} ({len(ordered)} packages, "
        f"{missing_license} without licence file)",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import logging
import re
import shutil
from pathlib import Path

import yaml

from . import config

log = logging.getLogger("l4rpness")

SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")


def roots() -> list[Path]:
    return [*config.SKILL_DIRS, config.DATA_DIR / "skills"]


def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}

    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return {}

    return data if isinstance(data, dict) else {}


def _read(path: Path) -> tuple[dict, str]:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}, ""

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return _frontmatter(text), parts[2].strip()

    return {}, text.strip()


def _entry(path: Path) -> dict | None:
    meta, body = _read(path)
    name = str(meta.get("name") or path.parent.name).strip()
    description = " ".join(str(meta.get("description") or "").split())
    if not name or not description:
        log.warning("skipping skill without name or description: %s", path)
        return None

    return {
        "name": name,
        "description": description,
        "dir": path.parent,
        "body": body,
        "editable": path.is_relative_to(config.DATA_DIR),
    }


def discover() -> list[dict]:
    found: dict[str, dict] = {}
    for root in roots():
        if not root.is_dir():
            continue
        for file in sorted(root.glob("*/SKILL.md")):
            entry = _entry(file)
            if entry is not None:
                found[entry["name"]] = entry
    return [found[name] for name in sorted(found)]


def load(name: str) -> dict | None:
    for entry in discover():
        if entry["name"] == name:
            return entry
    return None


def index(limit: int = 200) -> str:
    entries = discover()
    if not entries:
        return ""

    lines = [
        "<available_skills>",
        "Load a skill with the skill tool when the task matches its description.",
    ]
    for entry in entries:
        description = entry["description"]
        if len(description) > limit:
            description = description[: limit - 1].rstrip() + "…"
        lines.append(f"- {entry['name']}: {description}")
    lines.append("</available_skills>")
    return "\n".join(lines)


def save(name: str, description: str, body: str, overwrite: bool = False) -> dict:
    slug = name.strip().lower()
    if not SLUG.fullmatch(slug):
        raise ValueError("name must be a lowercase slug using a-z, 0-9 and dash")
    if not description.strip():
        raise ValueError("description is required")

    directory = config.DATA_DIR / "skills" / slug
    file = directory / "SKILL.md"
    if file.exists() and not overwrite:
        raise FileExistsError(slug)

    directory.mkdir(parents=True, exist_ok=True)
    front = yaml.safe_dump({"name": slug, "description": description.strip()}, sort_keys=False).strip()
    file.write_text(f"---\n{front}\n---\n\n{body.strip()}\n")
    return {"name": slug}


def delete(name: str) -> None:
    """Remove a custom skill and its directory.

    Built-ins live in the repo and cannot be removed here — `PermissionError`
    is raised so the caller can answer 403 rather than a misleading 404.
    """
    entry = load(name)
    if entry is None:
        raise FileNotFoundError(name)
    if not entry["editable"]:
        raise PermissionError(name)
    shutil.rmtree(entry["dir"])

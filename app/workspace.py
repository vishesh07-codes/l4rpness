"""Read-only access to a campaign's agent work directory.

The agent writes into ``config.WORK_DIR/<campaign_id>`` (see
``tools._work_dir``). These helpers expose it to the UI without ever leaving
that root — the same containment rule ``tools._resolve`` enforces for the
agent's own file tools.
"""

from pathlib import Path

from . import config

# Files larger than this are truncated when read as text for the viewer.
TEXT_CAP = 200_000


def root(campaign_id: int) -> Path:
    return config.WORK_DIR / str(campaign_id)


def _safe(campaign_id: int, name: str) -> Path:
    base = root(campaign_id).resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base):
        raise ValueError("path escapes the work dir")
    return path


def tree(campaign_id: int) -> list[dict]:
    base = root(campaign_id)
    if not base.is_dir():
        return []

    entries: list[dict] = []
    for path in sorted(base.rglob("*")):
        rel = str(path.relative_to(base))
        try:
            is_dir = path.is_dir()
            size = 0 if is_dir else path.stat().st_size
        except OSError:
            continue
        entries.append(
            {
                "path": rel,
                "name": path.name,
                "type": "dir" if is_dir else "file",
                "size": size,
            }
        )
    return entries


def read_text(campaign_id: int, name: str) -> dict:
    path = _safe(campaign_id, name)
    if not path.is_file():
        raise FileNotFoundError(name)

    data = path.read_bytes()
    truncated = len(data) > TEXT_CAP
    return {
        "path": name,
        "content": data[:TEXT_CAP].decode("utf-8", errors="replace"),
        "truncated": truncated,
        "size": len(data),
    }


def raw_path(campaign_id: int, name: str) -> Path:
    path = _safe(campaign_id, name)
    if not path.is_file():
        raise FileNotFoundError(name)
    return path

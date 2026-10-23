import re
from pathlib import Path

from fastapi import HTTPException, UploadFile

from . import config

TEXT_SUFFIXES = {
    ".txt", ".md", ".json", ".yaml", ".yml", ".xml", ".csv", ".log",
    ".har", ".ini", ".conf", ".cfg", ".js", ".ts", ".html", ".sql",
}

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif"}


def is_image(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_SUFFIXES


def _safe_name(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^\w.\- ]+", "_", name).strip(" .")
    return name or "file"


async def collect(files: list[UploadFile]) -> list[dict]:
    items = []
    for up in files:
        if not up.filename:
            continue
        data = await up.read()
        if not data:
            continue
        if len(items) >= config.MAX_FILES_PER_FIELD:
            raise HTTPException(400, f"max {config.MAX_FILES_PER_FIELD} files per field")
        if len(data) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(400, f"{up.filename} is too large (max 10 MB)")
        items.append({
            "filename": _safe_name(up.filename),
            "content_type": up.content_type or "",
            "size": len(data),
            "data": data,
        })
    return items


def store(campaign_id: int, field: str, item: dict) -> tuple[str, str]:
    folder = config.UPLOAD_DIR / str(campaign_id) / field
    folder.mkdir(parents=True, exist_ok=True)

    path = folder / item["filename"]
    n = 1
    while path.exists():
        path = folder / f"{path.stem}-{n}{path.suffix}"
        n += 1

    path.write_bytes(item["data"])
    return str(path.relative_to(config.UPLOAD_DIR)), path.name


def store_chat(campaign_id: int, item: dict) -> str:
    """Store a file attached to a chat message; returns the path relative to the upload dir."""
    folder = config.UPLOAD_DIR / str(campaign_id) / "chat"
    folder.mkdir(parents=True, exist_ok=True)

    path = folder / item["filename"]
    n = 1
    while path.exists():
        path = folder / f"{path.stem}-{n}{path.suffix}"
        n += 1

    path.write_bytes(item["data"])
    return str(path.relative_to(config.UPLOAD_DIR))


def prompt_text(rows) -> str:
    if not rows:
        return ""

    budget = config.TOTAL_CHAR_CAP
    parts = []
    for row in rows:
        suffix = Path(row["filename"]).suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            parts.append(
                f"### {row['filename']} (image, stored at {row['path']} "
                "relative to the upload dir; inspect it with your tools)"
            )
            continue
        if suffix not in TEXT_SUFFIXES:
            parts.append(f"### {row['filename']} (binary, stored at {row['path']})")
            continue

        try:
            with (config.UPLOAD_DIR / row["path"]).open(errors="replace") as fh:
                text = fh.read(config.FILE_CHAR_CAP + 1)
        except OSError:
            continue

        if len(text) > config.FILE_CHAR_CAP:
            text = text[: config.FILE_CHAR_CAP] + "\n[truncated]"
        if len(text) > budget:
            text = text[:budget] + "\n[truncated]"
        budget -= len(text)

        parts.append(f"### {row['filename']}\n{text}")
        if budget <= 0:
            parts.append("(further attachments omitted — context cap)")
            break

    return "Attached files:\n\n" + "\n\n".join(parts)

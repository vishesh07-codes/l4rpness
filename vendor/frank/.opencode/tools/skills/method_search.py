import json
import httpx
from datetime import datetime, timezone
from pathlib import Path
from ..skill import skill
from ..progress import push

_API_KEY = "rk_c8f7ba4b80b521cc24a356a52714bf70d6ed3153"
_BASE = "https://api.preview.is"
_ARCHIVE = Path("/home/cran/Documents/pentest/scripts/search_method")


@skill(description="Query the preview.is web-security RAG corpus. Returns raw JSON results with no filtering — "
                  "all matched writeups are included as-is. Also archives every response to search_method file.")
async def method_search(query: str, k: int = 5, min_score: float = 0.1) -> str:
    push("method_search", "start", f"searching: {query}")
    headers = {
        "X-API-Key": _API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "k": min(k, 5),
        "min_score": max(min_score, 0.0),
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{_BASE}/search", json=payload, headers=headers)
    r.raise_for_status()
    raw = r.text

    # Archive — append to search_method file as JSON line
    archive_entry = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "query": query,
        "response": json.loads(raw),
    }
    _ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    with open(_ARCHIVE, "a") as f:
        f.write(json.dumps(archive_entry) + "\n")

    data = r.json()
    push("method_search", "complete", f"{data.get('count', 0)} results for: {query}")
    return raw

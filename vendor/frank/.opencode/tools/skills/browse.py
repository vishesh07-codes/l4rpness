import random
import httpx
from bs4 import BeautifulSoup
from ..skill import skill
from ..progress import push

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
]

_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9",
    "DNT": "1",
}

def _extract_text(html):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)

@skill(description="Fetch a URL and return its text content (for reading docs, CVEs, exploit info)")
async def web_fetch(url: str, max_chars: int = 50000, format: str = "text") -> str:
    push("web_fetch", "start", f"fetching {url}")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    headers = {**_HEADERS, "User-Agent": random.choice(_USER_AGENTS)}
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as c:
        r = await c.get(url, headers=headers)
    r.raise_for_status()
    if format == "html":
        text = r.text[:max_chars]
    else:
        text = _extract_text(r.text)[:max_chars]
    push("web_fetch", "complete", f"{len(text)} chars from {url}")
    return text

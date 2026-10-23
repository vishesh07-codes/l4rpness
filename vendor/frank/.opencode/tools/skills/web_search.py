import httpx
from ..skill import skill
from ..progress import push

_API_KEY = "84eabbdb-b4e7-4b0b-8d62-b07181f4cb8a"
_BASE = "https://api.exa.ai"

@skill(description="Web search using EXA - returns relevant pages with titles, URLs, and text snippets")
async def web_search(query: str, num_results: int = 10) -> str:
    push("web_search", "start", f"searching: {query}")
    headers = {
        "x-api-key": _API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "numResults": min(num_results, 20),
        "useAutoprompt": True,
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{_BASE}/search", json=payload, headers=headers)
    r.raise_for_status()
    data = r.json()
    results = []
    for i, item in enumerate(data.get("results", []), 1):
        title = item.get("title", "No title")
        url = item.get("url", "")
        text = item.get("text", "")[:600]
        favicon = item.get("favicon", "")
        published = item.get("publishedDate", "")
        extra = ""
        if published:
            extra += f" [{published}]"
        results.append(f"{i}. [{title}]({url}){extra}{' ' + favicon if favicon else ''}\n   {text}")
    output = "\n\n".join(results) if results else f"No results for: {query}"
    push("web_search", "complete", f"{len(results)} results for: {query}")
    return output


@skill(description="EXA answer - AI-generated summary with citations for a query (like AI-powered search)")
async def web_answer(query: str, num_results: int = 5) -> str:
    push("web_answer", "start", f"answering: {query}")
    headers = {
        "x-api-key": _API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "numResults": min(num_results, 10),
        "text": True,
    }
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.post(f"{_BASE}/answer", json=payload, headers=headers)
    r.raise_for_status()
    data = r.json()
    answer = data.get("answer", "")
    citations = data.get("citations", [])
    lines = [answer.strip(), ""]
    if citations:
        lines.append("---")
        for i, cite in enumerate(citations, 1):
            title = cite.get("title", "")
            url = cite.get("url", "")
            lines.append(f"[{i}] {title}: {url}")
    output = "\n".join(lines)
    push("web_answer", "complete", f"{len(citations)} citations")
    return output

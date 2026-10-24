# Verification — Demo Web

Run against freshly seeded databases with all three apps live:

```bash
cd demo-web
rm -f */instance/*.db
python3 serve.py &
python3 verify_all.py
```

Result: **20/20 checks passed.**

```
PASS Quillworks 1 — second-order SQLi in tags                   hashes rendered in related notes
PASS Quillworks 2 — cross-workspace revision restore            private memo restored into own note
PASS Quillworks 3 — self role escalation                        editor became owner
PASS Quillworks 4 — stored XSS in comments                      payload preserved in img tag
PASS Quillworks 5 — SSRF in document import                     fetched internal store API
PASS Quillworks 6 — open redirect                               //evil.example/x
PASS Harbor Lane 7 — negative quantity checkout                 negative total, +$658 credit
PASS Harbor Lane 8 — gift-card double spend                     balance 20000 -> -20000
PASS Harbor Lane 9 — returns BOLA                               victim item refunded to attacker
PASS Harbor Lane 10 — predictable order reference               order 1 enumerated
PASS Harbor Lane 11 — leaked API key exports customers          4 rows
PASS Harbor Lane 12 — stored XSS in merchant reviews            iframe survives filter
PASS Harbor Lane 13 — SSRF in webhook tester                    internal page returned
PASS Harbor Lane 14 — SVG upload XSS                            served as image/svg+xml
PASS Meridian 15 — CSV export missing role check                full ticket dump as customer
PASS Meridian 16 — SQLi in sort (boolean oracle)                500 / 200
PASS Meridian 17 — internal notes in ticket API                 internal note returned to customer
PASS Meridian 18 — attachment path traversal                    app.py source returned
PASS Meridian 19 — forged remember-me cookie                    admin session without password
PASS Meridian 20 — blind XSS via User-Agent                     raw UA in admin audit table
```

## Browser checks

The two stored-XSS findings were additionally executed in a real browser (Playwright,
headless Chromium) because an HTML-only check cannot prove script execution:

- **Quillworks 4** — comment payload rendered as
  `<img src="x" onerror="alert&#40;document.domain&#41;" ...>`; opening the note as a
  workspace member triggered `alert("127.0.0.1")`.
- **Harbor Lane 12** — merchant review panel loaded the `iframe srcdoc`; dialog
  `127.0.0.1` fired in the merchant origin.
- **Harbor Lane 14** — `/uploads/<name>.svg` served as `image/svg+xml`; navigating to it
  fired `alert("127.0.0.1")`.
- **Meridian 20** — admin audit page rendered the stored User-Agent `<img onerror=...>`;
  dialog fired on page load.

## Visual check

Landing page, Quillworks marketing/home, Harbor Lane storefront, Meridian login and ticket
view were screenshotted and reviewed for layout integrity at 1440×900.

State was reset after verification: all `instance/*.db` files and upload folders were
deleted and regenerated from the seeders, so the shipped lab starts clean.

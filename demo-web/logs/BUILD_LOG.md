# Demo Web — build log

Working notes from building the lab. Newest entries at the bottom.

## Scope

Three applications in `demo-web/`, each with a full UI, its own SQLite database and seed
data, plus a landing page and a multi-app launcher.

Design goals:

- every vulnerability reachable from the running app with seeded accounts
- no signage, no "vulnerable" demo banners, no hints in the interface
- bugs that need more than one request, more than one account, or a bit of reasoning
- a mix of severities so the lab is worth revisiting

## Environment

- Python 3.14.7, Flask 3.1.3, Jinja2 3.1.6
- SQLite via the stdlib `sqlite3` module
- No ORM, no external services
- Playwright (MCP) used for browser-side verification of the two XSS classes

## Build

### 1. Quillworks (8801)

- `app.py`: session auth, workspaces/roles, notes + revisions, tags, comments, import, search,
  member management, small JSON API.
- Templates: landing, auth, dashboard, workspace, note, editor, members, settings, account,
  import, search, errors.
- Seed: 4 users, 4 workspaces, 8 notes, 2 revisions of a private memo, tags, comments absent
  by default.
- CSS: warm paper palette, serif headings, sidebar metadata on notes.

Verified deliberately:

- second-order SQLi — tag names are parameterised on write, concatenated on read in the
  related-notes query
- cross-workspace version restore — the restore endpoint checks note access, not that the
  version belongs to the note
- role change — the members endpoint lets a member patch their own role
- stored XSS — comments are tag-stripped on input, markdown is rendered after, so an image
  URL can break out of its attribute
- SSRF — document importer fetches any URL server-side
- open redirect — `next` only checked with `startswith('/')`

Verification: `curl` + a short Python script, then Playwright for the XSS. All passed.

### 2. Harbor Lane (8802)

- `app.py`: catalogue, cart cookie, checkout, gift cards, coupons, orders, returns, reviews,
  merchant portal, uploads, JSON API, webhook tester.
- Templates: storefront, product, cart, checkout, order, account, gift card, auth, merchant
  base + five merchant pages, developers, errors.
- Seed: 3 users, 1 store, 8 products, 5 reviews, 2 gift cards, 1 shipped order.
- CSS: navy/sand/amber, editorial product grid, merchant back office.

Verified deliberately:

- negative quantities accepted by the JSON cart endpoint while the web form clamps 1–99
- gift-card balance read → sleep → debit, so concurrent checkouts double-spend
- returns endpoint resolves the order item, then never compares the order owner to the
  session user
- order references are derived from the numeric id: `HL-{id:05d}-{(id*37+11)%1000}`
- the demo API key is shipped in `static/js/storefront.js` and has customer-export scope
- reviews are script/`on*`-filtered on write, but an `iframe srcdoc` with entity-encoded
  markup survives and executes in the merchant review panel
- webhook tester fetches arbitrary URLs
- product uploads accept `.svg` and serve it from the app origin without
  `Content-Disposition: attachment`

Verification: `curl`/`requests` for the API bugs, threads for the race (balance 20000 →
−20000 on two concurrent checkouts), Playwright for both XSS executions.

### 3. Meridian Support (8803)

- `app.py`: auth with optional remember cookie, ticket list/filter, thread, internal notes,
  attachments, admin overview/tickets/audit/people/settings, CSV export, JSON API.
- Templates: login, ticket list, ticket detail, new ticket, five admin pages, errors.
- Seed: 6 users (admin/2 agents/3 customers), 5 tickets, mixed public and internal messages.
- CSS: compact dark-rail SaaS layout.

Verified deliberately:

- CSV export has `@login_required` but no role check — a customer receives every ticket,
  requester email and internal note
- `sort` is split into field/direction; only the field is checked against the allow-list,
  the direction is interpolated raw. Error-based boolean oracle via
  `CASE WHEN (...) THEN abs(-9223372036854775808) ELSE 0 END`
- JSON ticket endpoint serialises every message, including internal notes; the HTML view
  filters them
- attachment download joins the URL path segment onto the upload directory, so `..%2F`
  escapes it
- remember-me cookie is `base64(uid + ":" + md5(uid))` with no signature and no expiry check
  on the server — uid 1 is the seeded admin
- audit log stores the User-Agent verbatim and renders the table from a `Markup` string;
  a login attempt with an `onerror` UA executes in the admin's browser

Verification: `curl`/`requests` for authz, leak, traversal, sort oracle, cookie forgery;
Playwright for the audit-log XSS.

## Fix / hardening pass not applied

These are intentionally vulnerable; nothing was patched after verification. The comments in
the source are neutral and do not label the bugs.

## Final state

- Databases reset after verification so the shipped lab starts clean.
- Landing page and launcher added (`serve.py`, `run.sh`, `index.html`).
- Answers documented in `SOLUTIONS.md` (20 findings across the three apps).

## Verification pass

- `verify_all.py` run against fresh databases: **20/20 passed**.
- The XSS findings were additionally executed in headless Chromium (Playwright); alerts
  fired in the reviewer origin, the merchant origin, the uploaded SVG origin and the admin
  audit page.
- Layout checked at 1440×900 for the landing page and each app's primary screen.
- Details in `logs/VERIFICATION.md`.

## Reset for delivery

- Stopped the stack, deleted every `instance/*.db` and uploaded proof file, restarted
  `serve.py`. Databases are recreated from the seeders on import, so the first user session
  sees the original dataset (no test orders, no injected reviews, no forged roles).

## Launcher hardening (follow-up)

Reported failure: `python3 ./run.sh` raised `ModuleNotFoundError: No module named 'flask'`
when the active `python3` was the project `.venv` (Python 3.12, no Flask), while
`/usr/bin/python3` had it installed in `~/.local`.

- `serve.py` no longer imports Flask. The landing page is a stdlib
  `ThreadingHTTPServer`.
- The launcher resolves a Flask-capable interpreter (`$PYTHON`, `python3`, `python`,
  `/usr/bin/python3`, `/usr/local/bin/python3`, local venvs) and uses it for the three
  apps. If none is found it creates `demo-web/.venv` and installs `requirements.txt`.
- Readiness probes compare `/healthz` app names; Harbor Lane and Meridian advertised names
  that did not match their module names, which made `wait_up` spin until timeout. Both now
  return `quillworks`/`harborlane`/`meridian` consistently.
- Re-running is idempotent: live apps are reported as *already running*, and foreign
  processes holding a port are reported instead of crashing.
- Verified with `PATH=.venv/bin:$PATH ./run.sh` (broken interpreter) — all four services
  start, and a second run exits 0 without duplicating them.

## Docker reachability

- `127.0.0.1` inside a container is the container's own loopback. With the apps bound to
  host loopback, `http://127.0.0.1:8801` from `l4rpness-l4rpness-1` cannot work.
- Added `HOST` support to all three apps and the launcher (`HOST=0.0.0.0 ./run.sh`).
- The compose service already declares `host.docker.internal:host-gateway`; the
  `l4rpness_default` gateway is `192.168.112.1`.
- Verified from inside the running container:
  `curl http://host.docker.internal:8801/healthz` →
  `{"app":"quillworks","status":"ok"}`, and the same via `192.168.112.1` for 8802/8803.
- For container-only exposure, `HOST=192.168.112.1 ./run.sh` binds just the bridge address.




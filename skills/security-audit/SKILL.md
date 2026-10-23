---
name: security-audit
description: >-
  Autonomous Grounded Vulnerability Discovery, Validation, and Remediation
  (AGVDVR). Transforms LLM from heuristic text generator into a Deterministic
  State Machine. Every finding is mathematically proven via a passing exploit
  in an isolated sandbox before being reported. Integrates SAST, dependency
  auditing, secret scanning, and dynamic exploit validation.
license: MIT
compatibility: opencode
metadata:
  audience: security-engineers
  domain: application-security
  category: penetration-testing
  version: "2.0.0"
  tags:
    - security-audit
    - vulnerability-assessment
    - penetration-testing
    - deterministic
    - sandbox-validated
    - remediation
    - sast
    - dependency-scanning
  references:
    - "https://cheatsheetseries.owasp.org"
    - "https://semgrep.dev/docs/writing-rules/overview/"
included_skills:
  - rules-before-pentest
  - x3r0-xss-hunt
---

# AGVDVR v2 — Autonomous Grounded Vulnerability Discovery, Validation & Remediation

**Core axiom: A vulnerability does not exist until it is mathematically proven by a passing exploit in an isolated, ephemeral sandbox.**

This skill transforms the LLM from a heuristic text generator (hallucination-prone, high false positives) into a **Deterministic State Machine**. The LLM acts as a reasoning engine that orchestrates external deterministic tools. Each phase has binary pass/fail outcomes — no "maybe" findings.

---

## 0. Skills Integration

This skill is designed to **load alongside** companion skills. Invoke them in sequence:

| Load Order | Skill | When to Use |
|------------|-------|-------------|
| 1 | `rules-before-pentest` | Before any audit — filters noise, enforces impact-first mindset, prevents wasted compute on theoretical bugs |
| 2 | `security-audit` (this skill) | The main pipeline — scan, hypothesize, prove, patch |
| 3 | `x3r0-xss-hunt` | During Phase 2 if you identify XSS as a candidate vector — load it for context-specific payload crafting and WAF bypass reasoning |

---

## 1. Tool Prerequisites

The following tools must be available in the environment. This skill maps abstract concepts from the AGVDVR framework to concrete tool calls.

| Abstract Function | Concrete Tool(s) | Purpose |
|-------------------|-------------------|---------|
| `get_scoped_context` | `frank_codebase_identify`, `glob`, `grep` | Discover codebase structure, languages, entry points. Filter to relevant files. |
| `query_vulnerability_database` | `frank_scan_vulns` (semgrep) | SAST scan — finds known vulnerability patterns in source code. Replaces the "RAG memory check." |
| `dynamic_analysis` | `frank_scan_secrets` (gitleaks) | Scan git history for hardcoded secrets, keys, tokens. |
| `dependency_audit` | `frank_audit_deps` (pip-audit / npm audit) | Check dependency tree for known CVEs. |
| `recon` | `frank_net_scan`, `frank_dns_lookup` | Network-level recon when the target is a live host. |
| `sbx_execute_poc` | `bash` (with Docker/container runtime) | Execute PoC scripts in ephemeral environments. Capture stdout/stderr/exit codes. |
| `sbx_apply_patch` | `edit`, `write`, `bash` (patch + re-run) | Apply proposed fix and re-run the PoC to verify closure. |

### Environment Requirements

The agent must confirm these are available before starting:

```bash
# Check for container runtime (PoC sandbox)
which docker || which podman

# Check for language-specific runners (Foundry, pytest, etc.)
which forge 2>/dev/null || which pytest 2>/dev/null || echo "No test framework found"

# Check for scanning tools
which semgrep 2>/dev/null || pip3 show semgrep 2>/dev/null
which gitleaks 2>/dev/null
pip3 show pip-audit 2>/dev/null || npm --version 2>/dev/null
```

If a sandbox runtime is missing, the audit is limited to static analysis only. Flag this in output.

---

## 2. The 6-Phase Execution Pipeline

### Phase 1: Reconnaissance & Context Injection

**Goal**: Understand what you're auditing before looking for bugs.

#### Step 1.1 — Codebase Identification

Run `frank_codebase_identify` on the target. Extract:
- Languages and frameworks
- Build system and dependency manager
- Entry points and architectural patterns
- Test framework (if any)

#### Step 1.2 — Diff Isolation (PR mode)

If auditing a pull request or specific commit, use `git diff` to isolate only changed lines:

```bash
git diff <base>...<head> -- '*.py' '*.js' '*.ts' '*.sol' '*.rs' | head -500
```

Then use `grep` and `glob` to find surrounding context (imports, helper functions, state variables).

#### Step 1.3 — Surface Scan

Run all three scanners in parallel:

```
├── frank_scan_vulns   — SAST (semgrep): finds injection, XSS, crypto misuse, auth flaws, etc.
├── frank_scan_secrets — Secret scanning (gitleaks): finds hardcoded keys, tokens, passwords
└── frank_audit_deps   — Dependency audit: finds known CVEs in third-party libs
```

#### Step 1.4 — Filter Noise

For each result from the scanners, apply the `rules-before-pentest` filter:
- Is this exploitable without prior access?
- Can I demonstrate it working right now?
- Would an adversary actually use this?

Discard anything that fails these checks. **Do not proceed to Phase 2 with noise.**

---

### Phase 2: Multi-Path Hypothesis Generation

**Goal**: Generate a ranked matrix of candidate vulnerability types based on Phase 1 data.

For EACH finding that survived Phase 1 filtering, construct a **Structured Hypothesis**:

```
Hypothesis Template:
----------------------
Vulnerability Type:  [e.g., SQL Injection, Path Traversal, Reentrancy]
Source:              [e.g., frank_scan_vulns semgrep rule X, manual analysis of file Y]
Code Location:       [file:line]
Root Cause:          [one sentence: what the code does wrong]
Attack Vector:       [how an attacker reaches the vulnerable code path]
Prerequisites:       [auth level, specific conditions, etc.]
Impact:              [what the attacker gains — be specific]
Estimated Severity:  [Critical/High/Medium/Low]
```

Generate hypotheses for ALL survivable findings. Rank them by impact × exploitability.

**Priority ordering** (highest to lowest):
1. Unauthenticated RCE / SQLi / command injection
2. Unauthenticated auth bypass / privilege escalation
3. Stored XSS with session theft chain
4. SSRF with demonstrated internal service access
5. Path traversal / file read
6. Reflected XSS (requires user interaction)
7. Dependency CVEs with working exploit
8. Hardcoded secrets (mark for rotation)

**If XSS appears in the candidate list**, load `x3r0-xss-hunt` to guide context analysis and payload crafting.

---

### Phase 3: The Grounding Engine (PoC Generation)

**Goal**: For the #1 hypothesis, write an executable Proof of Concept that proves or disproves the bug.

#### 3.1 — Select Target Hypothesis

Pick exactly **one** hypothesis. Do not proceed to any other until this one is resolved.

#### 3.2 — Write the PoC

The PoC must be a self-contained script that:

1. **Initializes** the target in a known state (deploy contract, start server, load fixture data)
2. **Executes** the attack via publicly exposed entry points only
3. **Asserts** the outcome with strict assertions

**Language-specific templates:**

```python
# Python / PyTest template
def test_exploit():
    # 1. Setup — realistic starting state
    target = TargetClass()
    initial_balance = target.get_balance(attacker_address)

    # 2. Attack — use only public/external entry points
    target.public_vulnerable_function(malicious_input)

    # 3. Assert — strict mathematical proof
    assert target.get_balance(attacker_address) > initial_balance, "Exploit failed"
```

```solidity
// Solidity / Foundry template
function testExploit() public {
    // 1. Setup
    vm.startPrank(attacker);
    uint256 initialBalance = address(attacker).balance;

    // 2. Attack — NO cheat codes to bypass memory/state
    target.vulnerableFunction{value: 1 ether}(payload);

    // 3. Assert
    assertGt(address(attacker).balance, initialBalance, "Exploit failed");
}
```

```bash
# Shell / curl template
#!/bin/bash
# 1. Setup
TOKEN=$(curl -s -X POST "$TARGET/auth/login" -d '{"user":"test","pass":"test"}' | jq -r '.token')

# 2. Attack — straight command injection via param
curl -s "$TARGET/api/exec?cmd=cat%20/etc/passwd" -H "Authorization: Bearer $TOKEN"

# 3. Assert — check for sensitive data in response
if curl -s "$TARGET/api/exec?cmd=id" | grep -q "uid="; then
  echo "[PASS] Command execution confirmed"
  exit 0
else
  echo "[FAIL] Exploit did not work"
  exit 1
fi
```

#### 3.3 — Anti-Cheat Enforcement

**Strictly prohibited** in PoCs:
- Overriding internal/private state directly (e.g., Foundry `vm.store`, `vm.etch`)
- Bypassing access control via test framework cheat codes
- Mocking internal functions that the real target would call
- Using debug or test-only methods not available in production

The exploit must use **only the target's publicly exposed API surface**.

#### 3.4 — Validation Check

Before execution, verify:
- [ ] PoC uses only public/external entry points
- [ ] PoC starts from a realistic state (no contrived setup)
- [ ] PoC has a strict assert (not just "no error")
- [ ] PoC is self-contained (single script, no external deps)

---

### Phase 4: Sandbox Execution & State Resolution

**Goal**: Execute the PoC and resolve the hypothesis deterministically.

#### 4.1 — Execute

Run the PoC in a sandboxed environment:

```bash
# For Python targets
cd /tmp/audit-$(date +%s) && \
  cp -r /path/to/target . && \
  cd target && \
  python3 -m pytest test_exploit.py -v 2>&1
```

```bash
# For Foundry/Solidity
forge test --match-test testExploit -vvv 2>&1
```

```bash
# For shell-based PoCs
bash exploit.sh 2>&1
```

#### 4.2 — State Resolution

Parse the output and branch deterministically:

| Exit Signal | stdout/stderr Pattern | Meaning | Action |
|-------------|----------------------|---------|--------|
| **State 1: Setup Error** | Compilation failure, missing imports, module not found, type error | PoC is malformed | **Self-correct**: read error, fix PoC, retry. Max 3 attempts. If still fails → discard hypothesis as unverifiable. |
| **State 2: Exploit Failed** | `[FAIL]`, `FAILED`, `AssertionError`, `exit code 1` (non-assert), transaction reverted | Target defended the attack | **Auto-invalidate**: hypothesis was **False Positive**. Discard silently. Go to Phase 2, pick next hypothesis. |
| **State 3: Exploit Succeeded** | `[PASS]`, `OK`, `exit code 0` with expected output, assertion passed | Bug is **Verified Critical** | Proceed to **Phase 5: Remediation** immediately. |
| **State 4: Timeout/OOM** | Killed, OOM, exit code 137, 124, `SIGKILL`, test hangs >30s | PoC is malformed or target is in infinite loop | **Halt**: this PoC is dangerous/flawed. Do NOT retry this hypothesis. Log as "unverifiable: resource exhaustion." Move to next hypothesis. |

#### 4.3 — Retry Logic

- **State 1 (Setup Error)**: Max 3 retries. Each retry must be a different fix attempt (not the same broken code).
- **State 4 (Timeout/OOM)**: Zero retries. Do not burn compute.
- **States 2 and 3**: Single attempt is sufficient.

---

### Phase 5: Automated Remediation & Regression Testing

**Goal**: Generate a patch that closes the verified vulnerability, and prove it works.

#### 5.1 — Patch Generation

Analyze the root cause (from the hypothesis) and generate a minimal fix:

- **Injection bugs**: Add input validation, parameterized queries, or context-aware escaping
- **Access control bugs**: Add authorization checks (`require()`, middleware, role guards)
- **State machine bugs**: Re-add missing state checks, enforce ordering
- **Crypto bugs**: Use correct primitives, constant-time comparison, proper IV handling
- **Secrets**: Remove from code, recommend environment variables / vault

The patch must be a precise file diff:

```diff
--- a/src/vulnerable.py
+++ b/src/vulnerable.py
@@ -10,7 +10,8 @@
 def execute_command(cmd):
-    os.system(cmd)
+    import subprocess
+    subprocess.run(cmd, shell=True, check=True)
```

#### 5.2 — The Reverse Test

Apply the patch to the sandbox and re-run the SAME PoC:

```bash
# Apply the patch
cd /tmp/audit-XXXX/target
patch -p1 < /tmp/fix.patch

# Re-run the exact same exploit
python3 -m pytest test_exploit.py -v 2>&1
```

#### 5.3 — Outcome Matrix

| Reverse Test Result | Meaning | Action |
|---------------------|---------|--------|
| PoC **FAILS** | Patch closed the vulnerability. **Success.** | Format and deliver the final report. |
| PoC **PASSES** | Patch is ineffective — exploit still works. | Rewrite the patch with a different strategy. Max 3 retries. If still fails, log as "partially remediated" with the best-effort patch. |
| PoC produces **new error** (unrelated) | Patch introduced a regression. | Note the new error, revise patch, retry. |

#### 5.4 — Patch Verification Checklist

- [ ] PoC now fails (exploit no longer works)
- [ ] Original functionality still works (run existing test suite if available)
- [ ] Patch is minimal (fixes the bug, does not rewrite the file)
- [ ] No new imports or dependencies introduced unless necessary

---

### Phase 6: Report Generation & Delivery

**Goal**: Produce a structured, actionable finding report.

#### 6.1 — Report Template

```markdown
## [SEVERITY] [Vulnerability Type] in [Location]

**Status:** Verified | Patched | Partially Patched

### Root Cause
[One-paragraph explanation of what the code does wrong]

### Exploitation Chain
1. [Step 1: Initial access / precondition]
2. [Step 2: Trigger the vulnerable code path]
3. [Step 3: Achieve impact]

### Impact
[Concrete business impact: data exposure, fund loss, account takeover, etc.]

### Verified PoC
```[language]
[The exact PoC script that produced the [PASS] state]
```

### Patch
```diff
[The diff that produced the [FAIL] state on re-run]
```

### Evidence
- SAST rule: [semgrep rule ID if applicable]
- CVE: [CVE-ID if applicable]
- [Link to raw output logs]
```

#### 6.2 — Delivery

Present the report in a structured format. Include all verified findings plus:

- **Summary table** of all hypotheses tested (verified, discarded, unverifiable)
- **False positive count** (how many hypotheses were invalidated in Phase 4)
- **Remaining risk** (any partially patched items)

---

## 3. Error Handling & Edge Cases

### 3.1 — No Sandbox Available

If no container runtime or test framework is available:
- Run Phase 1 fully (static scans are still valuable)
- Postpone Phases 3-5
- Flag in the report: "Findings are static-only — PoC validation was skipped due to missing sandbox"
- Rate all findings as one severity level lower than estimated (unverified)

### 3.2 — Target Has No Tests / Can't Be Executed

For libraries, firmware, or non-runnable code:
- Phase 3 PoCs become descriptive exploitation sequences (curl commands, SQL queries, step-by-step walkthroughs)
- Mark as "Verified: Static-Only" in the report
- Do not assign severity higher than High (Critical requires dynamic execution)

### 3.3 — All Hypotheses Fail

If every hypothesis hits State 2 (Exploit Failed):
- This is a **desirable outcome** — the target is well-defended
- Report: "No verified vulnerabilities found after testing N hypotheses"
- Include the list of what was tested (transparency)

### 3.4 — Zero Hypotheses Generated (Nothing Survives Phase 1-2)

If scanners found nothing and manual analysis reveals nothing:
- Report: "No vulnerabilities identified. Target appears secure against tested vectors."
- Include scanner configurations used
- Recommend expanding test surface in future audits

---

## 4. Audit Workflow Diagram

```
Phase 1: Recon
  ├── frank_codebase_identify → languages, entry points
  ├── frank_scan_vulns        → SAST findings
  ├── frank_scan_secrets      → secrets
  └── frank_audit_deps        → dependency CVEs
          │
          ▼
Phase 2: Hypotheses (ranked by impact)
          │
          ▼ (pick #1)
Phase 3: PoC Generation
          │
          ▼
Phase 4: Sandbox Execution
  ├── State 1 (Setup Error)  → fix PoC, retry ≤3
  ├── State 2 (Exploit Fail) → FALSE POSITIVE, discard
  ├── State 3 (Exploit Win)  → VERIFIED, proceed
  └── State 4 (Timeout/OOM)  → HALT, discard permanently
          │
          ▼ (State 3 only)
Phase 5: Patch + Reverse Test
  ├── Reverse Test FAILS → patch verified ✓
  └── Reverse Test PASSES → rewrite patch, retry ≤3
          │
          ▼
Phase 6: Report
  ├── Verified findings with PoC + patch
  ├── False positive count
  └── Remaining risk notes
```

---

## 5. When to Invoke This Skill

**Use when:**
- You are asked to "audit this codebase for security vulnerabilities"
- You need to validate a SAST scanner's findings with working exploits
- You're reviewing a pull request for security regressions
- You need a dependency audit + secret scan + SAST in one pass
- You need to produce a deliverable security assessment with verified PoCs
- You are testing a smart contract, web app, CLI tool, or API service

**Do NOT use when:**
- You only need to run a single scanner (just call the tool directly)
- The target has no executable test surface (pure config files, documentation)
- You are doing a network pentest with no code access (use standard pentest tools)
- You need to scan a live production system without isolation (the sandbox requirement prevents this — you need a local copy)

---

## 6. Refs & External Resources

| Resource | Use |
|----------|-----|
| OWASP Cheat Sheets | Injection prevention guidance for patch generation |
| OWASP ASVS | Severity classification reference |
| CVE Database | Existing vulnerability references |
| Target's existing test suite | Integration point for regression testing |
| Target's security policy | Expected behavior for vulnerability disclosure |

---

*This skill enforces the AGVDVR framework: deterministic state machine, sandbox-proof PoCs, and verified patches. Every finding is mathematically proven before delivery.*

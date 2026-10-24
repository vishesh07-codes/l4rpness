# Findings report — write it as you find it

`FINDINGS.md` at the root of the campaign work directory is the final client-facing report.
It is not a scratchpad and not an end-of-run artifact. It must be complete, current and
self-contained at every second of the engagement: if the operator stops the campaign right
after your next tool call, `FINDINGS.md` is what they send to the client.

## The rule

The moment a vulnerability is confirmed — working PoC, reproducible command, concrete
impact — append its full section to `FINDINGS.md` **before you do anything else**. Do not
finish the current hypothesis first. Do not wait for more findings. Do not wait for a
coverage milestone, a "reporting phase", or the operator to ask. There is no later: a
continuous campaign can be stopped at any moment and a goal campaign may end on the next
tool call.

If the campaign's Instructions name a **required** finding (a specific bug class, severity,
component or objective), that is the trigger: write its section the moment it is proven,
before any further testing. Every other confirmed finding is appended the same way as it
appears.

"Confirmed" means all three are true:

- a command or request that reproduces the issue was actually run and its output captured;
- the PoC is stored in the work directory and runs as written, start to finish;
- the impact is concrete — data, access, code execution, money, availability, account
  takeover. "Could", "might", "potentially" and "if an admin clicks" are not impact.

Anything weaker stays in your notes. It does not enter the report.

## Writing rules

1. Read `FINDINGS.md` first. If it does not exist, create it with the header and summary
   block below. Never overwrite a file you have not read. Use exactly `FINDINGS.md` at the
   work-directory root (uppercase) so the operator and the console always find it.
2. Append new findings; never rewrite the file from memory. Preserve every existing
   section byte-for-byte. `cat >> FINDINGS.md <<'EOF' ... EOF` is the safest append; a
   read_file + write_file cycle is fine if you keep the old content intact.
3. Keep the summary block at the top current: target, status, last update, counts by
   severity, and one table row per finding. Update it in the same write that adds the
   finding. A stale summary is a bug.
4. If a finding already has a section and you learn more — better PoC, higher severity,
   wider impact, new affected endpoint — edit that section in place and note the update.
   Never create a duplicate section for the same bug.
5. If a finding is disproven later, mark its section `RETRACTED — <reason>`. Do not delete
   it; the record matters.
6. One section per finding, in the format below. The report is append-only apart from the
   summary table, severity corrections and retractions.
7. Write for a client, not for yourself: full sentences, exact URLs and paths, the command
   that reproduces the issue, the observed output. No internal chatter, no "TODO", no "see
   the chat above".
8. Keep PoCs and evidence next to each other (`poc/`, `evidence/`) and reference them by
   relative path in backticks so the console renders them as links.
9. After writing, tell the operator in one line: `Reported: <title> — FINDINGS.md`. Do not
   paste the whole section into chat, and do not stop testing.
10. In supervisor mode you own `FINDINGS.md`. Accepted hunter findings go in before you
    dispatch anything else. In fast mode the solo agent owns it. Hunters never edit the
    root file; they write a ready-to-paste section under their own directory.
11. Every finding ships with a **fix prompt**: a self-contained block the operator can hand
    to a coding LLM that has the repository. Write it in the same pass as the finding, never
    as a later cleanup. It must name the root cause and the exact control to add, preserve
    legitimate behaviour, and end with testable acceptance criteria. Details below.

## The fix prompt

The fix prompt is the second deliverable of every finding. The operator pastes it into a
coding agent that can see the code but knows nothing about this campaign, so it has to stand
alone: no "see the finding above", no references to chat, no placeholders, no invented file
names or line numbers.

Write it in this shape:

- **Goal** — what to fix and why it matters, in one or two sentences.
- **Affected code** — exact file and line when you have source access; endpoint, parameter
  and handler when the test was black-box. If you did not see the source, say what you know
  and what must be located, without guessing.
- **Root cause** — the missing or incorrect control, not the symptom. "The tag list is
  concatenated into the `IN (...)` clause" beats "SQL injection exists".
- **Reproduction** — the exact command or request from the finding, and the current observed
  behaviour.
- **Required change** — the specific control to implement: parameterised query, server-side
  ownership check, contextual output encoding, allow-list, signature verification, and so
  on. Describe the required behaviour; include a concrete diff only when you are sure of it.
- **Constraints** — preserve the public API and legitimate behaviour, keep the change
  minimal and idiomatic for the stack, call out migrations or configuration, forbid
  unrelated refactors.
- **Acceptance criteria** — the exploit request now fails in the stated way (403, 400,
  escaped output, rejected upload), and a regression test covers that exact path.
- **Verify** — the command that replays the PoC after the fix and the result it must
  produce.

If the flaw lives in a third-party component or a pinned version, state whether the fix is
an upgrade, a configuration change or a virtual patch, and name a fixed version only if you
verified it.

## Report format

```markdown
# Penetration test findings — <target>
**Status**: in progress · **Last update**: <ISO8601>
**Scope**: <what was in scope, from the campaign>
**Summary**: <N> confirmed — <C> critical, <H> high, <M> medium, <L> low

| # | Finding | Severity | Asset | Auth |
|---|---------|----------|-------|------|
| 1 | <short title> | Critical | <endpoint> | none |

---

## Finding #1: <descriptive title>
**Severity**: Critical | High | Medium | Low
**Endpoint / asset**: <full URL, host, file or service>
**Auth required**: none | low-privileged account | <role>
**Confirmed**: <ISO8601>

**Impact**: <1-3 sentences of concrete harm. No "could" or "might".>

**PoC**:
```bash
# exact commands, runnable from the work directory
...
```

**Observed evidence**:
```text
# the relevant response / output that proves it
...
```

**Exploitation chain**:
1. <entry point>
2. <action>
3. <result — the concrete impact>

**Remediation**: <1-2 sentences on the fix for a human reader>

**Fix prompt** (paste into a coding agent with repository access):
```text
Goal: fix a confirmed <severity> <vulnerability class> in <component>.
Context: <language, framework, version and where this code runs>

Affected: <file:line, or endpoint + parameter + handler if the test was black-box>
Root cause: <the missing or incorrect control>
Reproduction: <exact command or request>
Current behaviour: <what happens now>
Required change: <the specific control(s) to implement>
Constraints: <behaviour and API to preserve, stack idioms, migrations>
Acceptance criteria:
- <the reproduction now returns the expected failure or escaped output>
- a regression test covers the exploit path and passes in CI
Verify: <command> -> <expected result after the fix>
Do not change unrelated code and do not weaken existing tests.
```

---
```

Keep the order stable: summary first, then findings in the order they were confirmed.
Never leave a placeholder like `<fill in later>` in a section you have written — an
incomplete section is worse than no section.

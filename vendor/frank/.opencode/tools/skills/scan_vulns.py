import os
import json
import subprocess
from ..skill import skill
from ..progress import push

SEMGREP = os.path.expanduser("~/.local/bin/semgrep")

SEVERITY_MAP = {
    2: "ERROR",
    1: "WARNING",
    0: "INFO",
}


@skill(description="Run semgrep SAST scan on a codebase. Detects SQL injection, XSS, command injection, path traversal, insecure deserialization, crypto misuse, auth issues, and more across 30+ languages. Results are grouped by severity with file:line references.")
async def scan_vulns(path: str = ".", severity: str = "", config: str = "auto", language: str = "") -> str:
    push("scan_vulns", "start", f"scanning {path} config={config} severity={severity}")
    abspath = os.path.abspath(path)
    if not os.path.isdir(abspath):
        return json.dumps({"error": "directory not found"})

    if not os.path.isfile(SEMGREP):
        return json.dumps({"error": "semgrep not found, run: pip install semgrep"})

    cmd = [SEMGREP, "scan", "--json", "--quiet", "--no-rewrite-rule-ids"]

    if config == "auto":
        cmd.extend(["--config=auto"])
    elif config == "default":
        cmd.extend(["--config=p/default"])
    elif config == "owasp-top-ten":
        cmd.extend(["--config=p/owasp-top-ten"])
    elif config == "r2c":
        cmd.extend(["--config=p/r2c"])
    elif config == "cwe-top-25":
        cmd.extend(["--config=p/cwe-top-25"])
    elif config == "community":
        cmd.extend(["--config=p/community"])
    elif config == "python":
        cmd.extend(["--config=p/python"])
    elif config == "javascript":
        cmd.extend(["--config=p/javascript"])
    elif config == "java":
        cmd.extend(["--config=p/java"])
    elif config == "go":
        cmd.extend(["--config=p/golang"])
    elif config == "rust":
        cmd.extend(["--config=p/rust"])
    else:
        cmd.append(f"--config={config}")

    if language:
        cmd.extend(["--lang", language])

    cmd.append(abspath)

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "scan timed out after 300s", "target": abspath})

    if r.returncode not in (0, 1):
        return json.dumps({
            "error": f"semgrep exited with code {r.returncode}",
            "stderr": r.stderr[:500],
            "target": abspath,
        })

    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return json.dumps({
            "error": "failed to parse semgrep output",
            "stderr": r.stderr[:500],
            "target": abspath,
        })

    results = data.get("results", [])
    filtered = []
    severity_filter = severity.upper() if severity else ""

    for res in results:
        sev = SEVERITY_MAP.get(res.get("extra", {}).get("severity", 0), "INFO")
        if severity_filter and sev != severity_filter:
            continue
        extra = res.get("extra", {})
        start = res.get("start", {})
        filtered.append({
            "severity": sev,
            "rule_id": res.get("check_id", ""),
            "message": extra.get("message", "").split("\n")[0],
            "file": res.get("path", ""),
            "line": start.get("line", 0),
            "column": start.get("col", 0),
            "snippet": extra.get("lines", "").strip(),
            "cwe": extra.get("metadata", {}).get("cwe", ""),
            "confidence": extra.get("metadata", {}).get("confidence", ""),
            "fix": extra.get("fix", ""),
        })

    by_severity = {"ERROR": [], "WARNING": [], "INFO": []}
    for f in filtered:
        by_severity[f["severity"]].append(f)

    result = {
        "tool": "scan_vulns",
        "config": config,
        "target": abspath,
        "summary": {
            "errors": len(by_severity["ERROR"]),
            "warnings": len(by_severity["WARNING"]),
            "info": len(by_severity["INFO"]),
            "total": len(filtered),
        },
        "findings": filtered,
        "semgrep_errors": data.get("errors", []),
    }

    out = json.dumps(result, indent=2)
    push("scan_vulns", "complete", f"{len(filtered)} findings ({result['summary']['errors']} errors, {result['summary']['warnings']} warnings)")
    return out

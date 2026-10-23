import os
import json
import subprocess
import tempfile
from ..skill import skill
from ..progress import push

GITLEAKS = os.path.expanduser("~/go/bin/gitleaks")


@skill(description="Scan a git repository for hardcoded secrets, API keys, tokens, private keys, and credentials using gitleaks. Detects AWS keys, GitHub tokens, private keys, passwords, and high-entropy strings across commit history.")
async def scan_secrets(path: str = ".", entropy: float = 0.0) -> str:
    push("scan_secrets", "start", f"scanning {path}")
    abspath = os.path.abspath(path)
    if not os.path.isdir(abspath):
        return json.dumps({"error": "directory not found"})

    if not os.path.isfile(GITLEAKS):
        return json.dumps({"error": "gitleaks not found, run: go install github.com/zricethezav/gitleaks@latest"})

    git_dir = os.path.join(abspath, ".git")
    if not os.path.isdir(git_dir):
        return json.dumps({"error": "not a git repository", "target": abspath})

    report_fd, report_path = tempfile.mkstemp(suffix=".json")
    os.close(report_fd)

    cmd = [GITLEAKS, "--repo-path", abspath, "--report", report_path]
    if entropy > 0:
        cmd.extend(["--entropy", str(entropy)])

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        os.unlink(report_path)
        return json.dumps({"error": "scan timed out after 120s", "target": abspath})

    findings = []
    try:
        with open(report_path) as f:
            findings = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        pass

    os.unlink(report_path)

    if not isinstance(findings, list):
        findings = []

    deduped = {}
    for f in findings:
        key = (f.get("file", ""), f.get("offender", ""), f.get("commit", ""))
        if key not in deduped:
            deduped[key] = {
                "file": f.get("file", ""),
                "line": f.get("line", ""),
                "offender": f.get("offender", ""),
                "secret_type": f.get("reason", ""),
                "commit": f.get("commit", ""),
                "commit_msg": f.get("commitMsg", ""),
                "author": f.get("author", ""),
                "date": f.get("date", ""),
            }

    unique_findings = list(deduped.values())

    result = {
        "tool": "scan_secrets",
        "target": abspath,
        "summary": {"total": len(unique_findings)},
        "findings": unique_findings,
    }

    out = json.dumps(result, indent=2)
    push("scan_secrets", "complete", f"{len(unique_findings)} secrets found")
    return out

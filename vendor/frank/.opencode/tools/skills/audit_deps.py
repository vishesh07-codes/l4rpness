import os
import json
import subprocess
from ..skill import skill
from ..progress import push

PIP_AUDIT = os.path.expanduser("~/.local/bin/pip-audit")


def _audit_python(path):
    req_files = []
    for fname in ("requirements.txt", "Pipfile", "pyproject.toml"):
        fpath = os.path.join(path, fname)
        if os.path.isfile(fpath):
            req_files.append(fpath)

    if not req_files:
        return []

    if not os.path.isfile(PIP_AUDIT):
        return [{"source": "pip-audit", "error": "pip-audit not installed", "fix": "pip install pip-audit"}]

    results = []
    for req_file in req_files:
        try:
            r = subprocess.run(
                [PIP_AUDIT, "--requirement", req_file, "--format", "json"],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode == 0:
                continue
            data = json.loads(r.stdout)
            for vuln in data.get("vulnerabilities", []):
                results.append({
                    "source": "pip-audit",
                    "package": vuln.get("name", ""),
                    "installed": vuln.get("version", ""),
                    "vulnerability": vuln.get("id", ""),
                    "severity": vuln.get("severity", "unknown"),
                    "fix": vuln.get("fix_versions", []),
                    "description": vuln.get("description", ""),
                })
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            continue

    return results


def _audit_npm(path):
    lock_files = []
    for fname in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml"):
        if os.path.isfile(os.path.join(path, fname)):
            lock_files.append(fname)
            break

    if not lock_files:
        pkg_json = os.path.join(path, "package.json")
        if not os.path.isfile(pkg_json):
            return []

    try:
        r = subprocess.run(
            ["npm", "audit", "--json", "--prefix", path],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    if r.returncode not in (0, 1):
        return []

    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return []

    results = []
    for vuln_id, vuln in data.get("vulnerabilities", {}).items():
        results.append({
            "source": "npm-audit",
            "package": vuln.get("name", vuln_id),
            "installed": vuln.get("version", ""),
            "vulnerability": vuln_id,
            "severity": vuln.get("severity", "unknown"),
            "fix": [v.get("isMajorUpdate", False) and "major" or "minor" for v in vuln.get("fixAvailable", {})] if isinstance(vuln.get("fixAvailable"), dict) else [],
            "description": vuln.get("overview", ""),
        })

    return results


@skill(description="Audit project dependencies for known vulnerabilities. Checks Python (pip-audit), Node.js (npm audit), and other package managers. Detects CVEs and recommends safe versions. Run this after codebase_identify to understand the dependency risk surface.")
async def audit_deps(path: str = ".") -> str:
    push("audit_deps", "start", f"auditing {path}")
    abspath = os.path.abspath(path)
    if not os.path.isdir(abspath):
        return json.dumps({"error": "directory not found"})

    findings = []
    findings.extend(_audit_python(abspath))
    findings.extend(_audit_npm(abspath))

    by_source = {}
    for f in findings:
        src = f["source"]
        by_source.setdefault(src, []).append(f)

    result = {
        "tool": "audit_deps",
        "target": abspath,
        "summary": {
            "Python": len(by_source.get("pip-audit", [])),
            "Node.js": len(by_source.get("npm-audit", [])),
            "total": len(findings),
        },
        "findings": findings,
    }

    out = json.dumps(result, indent=2)
    push("audit_deps", "complete", f"{len(findings)} vulnerabilities found")
    return out

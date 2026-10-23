import socket
import subprocess
import json
from ..skill import skill
from ..progress import push

@skill(description="TCP port scan a target host using specter")
async def net_scan(host: str, ports: str = "") -> str:
    push("net_scan", "start", f"scanning {host} ports {ports or 'top 1000'}")
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out_path = f.name
    cmd = ["specter", "scan", host, "-S", "-q", "-N", "-o", out_path]
    if ports:
        cmd.insert(3, "-p")
        cmd.insert(4, ports)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        result = subprocess.run(
            ["cat", out_path], capture_output=True, text=True
        )
        data = json.loads(result.stdout)
        if data["open_ports"]:
            lines = [f"{p}/open/tcp" for p in data["open_ports"]]
            result = "\n".join(lines)
        elif data["errors"]:
            result = "\n".join(data["errors"])
        else:
            result = "no open ports found"
        push("net_scan", "complete", result[:200])
        return result
    finally:
        subprocess.run(["rm", "-f", out_path])

@skill(description="DNS lookup for a hostname")
async def dns_lookup(hostname: str, record_type: str = "A") -> str:
    push("dns_lookup", "start", f"resolving {record_type} records for {hostname}")
    if record_type == "A":
        try:
            ips = socket.getaddrinfo(hostname, None, socket.AF_INET)
            seen = set()
            for i in ips:
                seen.add(i[4][0])
            result = "\n".join(seen) if seen else "no records"
        except Exception as e:
            result = f"error: {e}"
    else:
        r = subprocess.run(["dig", "+short", "-t", record_type, hostname],
                           capture_output=True, text=True, timeout=10)
        result = r.stdout.strip() or "no records"
    push("dns_lookup", "complete", result[:200])
    return result

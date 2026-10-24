#!/usr/bin/env python3
"""Start every demo app on its own port and serve the landing page.

    python3 serve.py            # or ./run.sh

8800  landing
8801  Quillworks
8802  Harbor Lane
8803  Meridian Support

The launcher itself only uses the standard library. It looks for a Python that can
import Flask and uses that for the three apps, so running this with a bare venv
interpreter is fine.
"""

import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
APPS = [
    ("quillworks", 8801),
    ("harborlane", 8802),
    ("meridian", 8803),
]
LANDING_PORT = 8800
HOST = os.environ.get("HOST", "127.0.0.1")
spawned = []

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:  # noqa: BLE001 - older interpreters
    pass


# ---------------------------------------------------------------- interpreter

def has_flask(python):
    try:
        r = subprocess.run([python, "-c", "import flask"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        return r.returncode == 0
    except Exception:  # noqa: BLE001 - missing binary, timeout, permissions
        return False


def find_python():
    """Return a Python executable that can import Flask, installing one if needed."""
    if importlib.util.find_spec("flask") is not None:
        return sys.executable, False

    seen = set()
    candidates = [
        os.environ.get("PYTHON"),
        shutil.which("python3"),
        shutil.which("python"),
        "/usr/bin/python3",
        "/usr/local/bin/python3",
        os.path.join(BASE, ".venv", "bin", "python"),
        os.path.join(os.path.dirname(BASE), ".venv", "bin", "python"),
    ]
    for cand in candidates:
        if not cand or cand in seen or not os.path.isfile(cand):
            continue
        seen.add(cand)
        if has_flask(cand):
            return cand, False

    # Nothing usable: build a private venv and install the two requirements.
    venv_dir = os.path.join(BASE, ".venv")
    venv_py = os.path.join(venv_dir, "bin", "python")
    if not os.path.isfile(venv_py):
        print("No Python with Flask found. Creating demo-web/.venv ...")
        subprocess.run([sys.executable, "-m", "venv", venv_dir], check=False)
    if os.path.isfile(venv_py):
        req = os.path.join(BASE, "requirements.txt")
        print("Installing Flask into demo-web/.venv ...")
        r = subprocess.run([venv_py, "-m", "pip", "install", "-q", "-r", req])
        if r.returncode == 0 and has_flask(venv_py):
            return venv_py, True
    return None, False


# --------------------------------------------------------------------- probes

def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def health(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=0.7) as r:
            return json.loads(r.read().decode())
    except Exception:  # noqa: BLE001 - not our service
        return None


def state(port, app_name=None):
    """ours | other | free"""
    if not port_open(port):
        return "free"
    j = health(port)
    if not j:
        return "other"
    if app_name:
        return "ours" if j.get("app") == app_name else "other"
    return "ours" if "apps" in j else "other"


def wait_up(port, app_name=None, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if state(port, app_name) == "ours":
            return True
        time.sleep(0.25)
    return False


# -------------------------------------------------------------------- landing

class LandingHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE, **kwargs)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            body = json.dumps({"status": "ok", "apps": [n for n, _ in APPS]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def log_message(self, fmt, *args):
        pass


# ----------------------------------------------------------------------- main

def shutdown(*_args):
    for p in spawned:
        if p.poll() is None:
            p.terminate()
    raise SystemExit(0)


def main():
    python, made_venv = find_python()
    if not python:
        print("No Python with Flask could be found.\n"
              "Install it for this interpreter and try again:\n"
              "  %s -m pip install -r %s" % (sys.executable, os.path.join(BASE, "requirements.txt")))
        return 1

    if python != sys.executable:
        print("Using interpreter: %s" % python)
    if made_venv:
        print("(created demo-web/.venv for the dependencies)")

    log_dir = os.path.join(BASE, "logs")
    os.makedirs(log_dir, exist_ok=True)

    for name, port in APPS:
        where = state(port, name)
        if where == "ours":
            print("  %-12s http://127.0.0.1:%d  already running" % (name, port))
            continue
        if where == "other":
            print("  %-12s http://127.0.0.1:%d  PORT IN USE by another process" % (name, port))
            continue
        env = dict(os.environ, PORT=str(port), HOST=HOST, PYTHONUNBUFFERED="1")
        log = open(os.path.join(log_dir, "%s.log" % name), "ab", buffering=0)
        spawned.append(subprocess.Popen(
            [python, "app.py"], cwd=os.path.join(BASE, name),
            env=env, stdout=log, stderr=subprocess.STDOUT))
        ok = wait_up(port, name)
        print("  %-12s http://127.0.0.1:%d  %s" % (name, port, "up" if ok else "FAILED"))

    where = state(LANDING_PORT)
    print("  %-12s http://127.0.0.1:%d%s  (bind %s)" % (
        "landing", LANDING_PORT, "  already running" if where == "ours" else "", HOST))

    if where == "other":
        print("  landing port is in use by another process; skip the landing page")
        return 0
    if where == "ours":
        return 0

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    print("Ctrl+C stops the apps started by this command.")
    server = ThreadingHTTPServer((HOST, LANDING_PORT), LandingHandler)
    try:
        server.serve_forever()
    finally:
        shutdown()


if __name__ == "__main__":
    sys.exit(main() or 0)

import sys
import os
import asyncio
import json
import threading
import socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".opencode"))

from mcp.server.fastmcp import FastMCP
from tools.skill import discover_skills
from tools.progress import _queue

BASE = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = os.path.join(BASE, ".opencode/tools/dashboard.html")

mcp = FastMCP("frank")

for tool_name, info in discover_skills():
    mcp.add_tool(info["fn"], name=tool_name, description=info["description"])

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            while True:
                try:
                    event = _queue.get()
                    self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
                    self.wfile.flush()
                except BrokenPipeError:
                    return
                except OSError:
                    return
        elif self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            try:
                with open(DASHBOARD) as f:
                    self.wfile.write(f.read().encode())
            except FileNotFoundError:
                self.wfile.write(b"dashboard not found")
        else:
            self.send_response(404)
            self.end_headers()

def run_http():
    port = int(os.environ.get("FRANK_PORT", "8080"))
    for attempt in range(3):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port = 0
    else:
        return
    actual_port = server.server_address[1]
    os.environ["FRANK_PORT"] = str(actual_port)
    print(f"dash: http://127.0.0.1:{actual_port}", file=sys.stderr, flush=True)
    server.serve_forever()

t = threading.Thread(target=run_http, daemon=True)
t.start()

asyncio.run(mcp.run_stdio_async())

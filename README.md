# l4rpness

A local harness for black-box pentest campaigns. You point it at a target you are allowed to test, give it scope and context, and it keeps testing on its own.

Current state: dashboard, campaigns, campaign chat, and the agent can run tools. It executes shell commands and reads and writes files in a per-campaign work directory. Everything runs in Docker on your machine.

## Running it

    cp .env.example .env
    # put your DeepSeek key in .env
    docker compose up --build -d

Model endpoints, effort and the runtime limits live in `config.toml` and are editable on the Settings page. Endpoint edits (name, base URL, API key env var, thinking) are written straight into the file; runtime knobs apply immediately and are written back too when the container can write to the mounted file (comments and unrelated keys are preserved), and hand edits to the file are picked up automatically. The server address and port still need an edit plus `docker compose restart`.

Open http://127.0.0.1:4517

To wipe all campaigns, messages and uploads:

    docker compose down -v

To get a shell inside the container (you land as root; add `-w /app/data/work/<id>` to land in a campaign work dir):

    docker compose exec l4rpness bash

## Where this is going

The end product is something you start once and leave alone. You create a campaign: target, scope, notes, and anything that helps the agent understand the app, like a HAR file from a logged in session or an OpenAPI spec. After that it works without you.

A few supervisors run in parallel, each owning a slice of the surface. One takes the web app, another takes the API, another takes the hosts and ports. Supervisors plan and assign work. Workers do the actual testing: scans, HTTP requests, fuzzing, payloads, all with real tools. When a worker thinks it found something, it has to prove it with a real request and response. A separate verifier then replays that proof from a clean context, with no knowledge of how the finding was produced. If the replay works the finding is real. If it does not, it is thrown out. Nothing gets reported just because a model said it looked vulnerable.

Work comes from a coverage map. Every asset, endpoint and test type is a cell with a status: untested, queued, clean, blocked, or has a finding. The scheduler always knows what has not been tried and picks from that. This is also what keeps a 24/7 run from spinning in circles. New endpoints, changed pages and fixed bugs create new cells or reopen old ones. When a target changes, the map changes with it.

The dashboard is the only interface and the only notification system. It shows:

- campaigns and their live state
- the agent tree, so you can see what each supervisor and worker is doing right now
- live logs of every turn and tool call
- the requests and responses behind every finding
- findings with severity, status, and the replay evidence that verified them
- the coverage map, so you can see the blank spots

It does not stop by itself. It stops when you pause or kill it. Right now there is no budget cap, that can be added later if the API bill gets annoying.

## What works right now

- Create a campaign with a target, info, instructions, description and scope. Only the target is required, and it does not have to be a URL. You can paste a paragraph.
- Every field can take file attachments. Text files (notes, logs, HAR, OpenAPI) get their contents passed to the agent when it replies.
- Pick the model and the effort per campaign. The model list comes from the DeepSeek API; effort is low, high or max.
- Chat streams as it is written and replies render markdown as they arrive. Thinking is shown above every answer, always, and the full history including thinking is stored in SQLite.
- The agent runs tools: bash for shell commands, read_file and write_file for files, all inside a per-campaign work directory. Long command output is saved to a file the model can page through. Every tool call shows up in the chat as a card you can expand.
- The agent drives a real headless Chromium through the Playwright MCP server (`playwright_*` tools): navigate, read pages as accessibility snapshots, click and fill forms, run JavaScript, inspect network traffic, take screenshots. It runs inside the container, so JavaScript-heavy and WAF-protected targets can be tested the way a browser would.
- Skills: the agent sees the list of available skills in its prompt and loads the matching one with the skill tool. Built-ins live in `skills/` in the repo, custom ones are created and edited on the Skills page and stored in the data volume.

## How it is built

One container, one Python process.

```
browser  ->  FastAPI (127.0.0.1:4517)
               |-- /api/*          JSON endpoints
               |-- everything else serves the built React app
               |
               |-- SQLite          campaigns, messages, attachments
               |-- uploads dir     files attached to campaigns
               |-- DeepSeek API    one call per chat message
```

Backend, Python and FastAPI:

- `app/main.py` starts the app and serves the frontend
- `app/routes.py` has the API
- `app/db.py` creates and connects to SQLite. Plain sqlite3, WAL mode, no ORM
- `app/uploads.py` validates files, stores them, extracts text for the prompt
- `app/agent.py` runs the model and tool loop, streaming thinking, content and tool events
- `app/tools.py` has the tools the agent can call and their limits
- `app/mcp.py` connects external tool servers over MCP: frank's recon and scan tools, and the headless Playwright browser
- `app/skills.py` finds SKILL.md files and feeds the skill index to the prompt
- `skills/` holds the built-in playbooks
- `prompts/operator_policy.md` is appended to every campaign agent prompt as the operator directive. `AGENTS.md` is the repo guide for coding agents, not injected at runtime
- `config.toml` holds every setting, `.env` holds only the API key
- `prompts/campaign_agent.md` is the system prompt. Edit it without touching code

Frontend, React and TypeScript with Tailwind and shadcn components:

- `/` dashboard with the campaign list
- `/campaigns/new` the create form
- `/campaigns/:id` campaign details and the chat
- `/skills` the skill list, viewer and editor

The agent runtime is the next thing to build, and it is our own loop, no framework. The loop sends the conversation plus tool definitions to DeepSeek, runs whatever tools come back, appends the results and goes around again. Planned tools: shell, HTTP requests, file read and write, and a headless browser later. On top of that, state tools so agents write tasks, findings, hypotheses and coverage straight to the database instead of us trying to parse prose out of their replies.

The parts around the agents are deliberately not LLM driven. A small coordinator owns the agent tree, heartbeats, restarts and task assignment. If a supervisor dies it gets restarted from what is in the database. When a context gets too long it is summarized and the agent is reborn with a handoff, so nothing important lives only in a model's memory.

Docker: multistage build where Node builds the frontend and Python runs the app. Runs as root inside a hardened container (see Container tools), port 4517 bound to 127.0.0.1 only, all data in a named volume.

## Container tools

The image ships a small network toolkit so bash has something to work with: curl, wget, jq, dnsutils, nmap, netcat-openbsd, whois, iputils-ping and traceroute. The list lives in `tools.txt`, one line, comma separated. Edit it and rebuild:

    docker compose up --build -d

The image also ships a headless Chromium with the [Playwright MCP](https://github.com/microsoft/playwright-mcp) server, which the agent uses through the `playwright_*` tools. It is one shared browser per container, kept in memory (`--isolated`), logged out at start, and it closes itself after an hour without use. To skip it, build the minimal toolset above.

For a slim image without them:

    L4RP_TOOLSET=minimal docker compose up --build -d

The container runs as root **inside** the container: the agent can install packages at runtime (`apt-get`, `pip`, `npm`) and raw-socket tools work (`nmap -sS`, `nmap -O`, `tcpdump`). `sudo` is installed and passwordless.

Root is kept contained by the container boundary. `compose.yaml` pins `user: "0:0"`, drops all capabilities and adds back only what tooling needs (no `SYS_ADMIN`, no `SYS_MODULE`), sets `no-new-privileges`, keeps Docker's default seccomp profile, and mounts neither the Docker socket nor host paths beyond the project's config files. Private pid, network and mount namespaces mean root inside cannot see or reach the host. For an extra layer against kernel exploits, run the daemon with `userns-remap` or use rootless Docker (both are daemon-wide settings).

## Development

    uv venv && uv pip install -r requirements.txt -r requirements-dev.txt
    L4RP_RELOAD=1 .venv/bin/python -m app

    cd web && bun install && bun run dev

The Vite dev server proxies /api to port 4517.

## Kernel-pentest VM (optional)

A campaign can be given its own Linux VM for kernel work — LPE, and QEMU escape
attempts. It boots **this machine's own kernel** (`vmlinuz` + `/lib/modules`), so
`uname -r` inside the guest is the same kernel you are running: a bug proven
there is a bug here. Root is locked by default, so escalation has to be a real
exploit rather than a `su`.

The hypervisor runs on the host, never in the container. The container gets one
unix socket and nothing else — no images, no console files, no guest IP — so a
guest that escapes QEMU lands in an unprivileged host account instead of the
harness.

    vm/build-base.sh              # once: builds vm/base/base.qcow2
    .venv/bin/python vm/vmctl.py serve    # keep this running

Then tick **Kernel pentest** when creating a campaign. `vmctl doctor` prints the
host prerequisites, and `vmctl doctor --boot-check` boots the base once and
reads the kernel back to confirm parity.

Things worth knowing:

- The guest has no network and no compiler. Compile PoCs in the container
  (`gcc -static`) and push them in, or write the source in the guest and run it
  with `sh`.
- Panics are expected. The VM restarts itself with the same disk, and the
  serial console — which is what `vm_console` reads — is where the oops shows
  up. `dmesg` inside the guest is restricted exactly as it is on the host.
- Hunters share one VM, and one filesystem, with no separation inside it.
- Rebuilding the base needs every campaign's VM stopped first: a running clone
  holds a read lock on it.

## Tests

    .venv/bin/pytest

51 tests cover the API, the config loader, the tools, skills and the agent loop, all offline. The DeepSeek call is stubbed.

## Roadmap

- [x] dashboard, campaigns, campaign chat
- [x] shell and file tools in the agent loop
- [ ] http_request tool with response capture
- [x] campaign runner with supervisors that never stop and workers per task
- [ ] coverage map and the scheduler that picks work from it
- [ ] verifier that replays findings
- [x] skills (SKILL.md, loaded on demand, Skills page)
- [x] browser tool (Playwright MCP server inside the container), MCP servers
- [ ] the boring 24/7 parts: watchdog, resume after crash, log rotation

The control plane has to be running for any of this to work — without it,
kernel campaigns log "VM control plane unavailable" and their VM tools fail
(promptly, with that message, rather than hanging). Installing it as a user
service keeps it up without a terminal:

    vm/install-service.sh            # install, enable, start
    vm/install-service.sh status
    journalctl --user -u l4rpness-vm

The supervisor retries the VM on later chains, so a control plane that comes up
after a campaign started heals itself.

### Surviving a crash or a rebuild

The VM runs on the host, so the container's lifecycle barely touches it:

- **Container crash / rebuild** — the guest keeps running with its RAM and disk
  intact; the container reconnects to the socket and carries on. Nothing on the
  container's filesystem holds VM state, and the migration that adds the VM
  columns runs on startup, so existing campaigns are unaffected.
- **Control plane restart or crash** — guests are *adopted*, not killed: they are
  found by pid from their keyfile in `vm/state/`, the console reconnects, and the
  password and flag are read back from the keyfile. The unit uses `KillMode=process`
  precisely so a restart does not take the guests with it.
- **Host reboot** — the guests are gone. Their disk, and the password and flag
  they were configured with, survive in the keyfile; they are marked `interrupted`
  (as opposed to `stopped`, which means someone stopped them on purpose) and the
  watchdog brings them back, honouring the memory and slot budget. A guest over
  budget is held and retried rather than lost.
- Campaigns that retry a missing control plane: the supervisor re-checks on each
  chain, so a control plane that comes up late heals without a restart.

Only `vm/run/` is mounted into the container, and only the socket is ever in it —
images, keyfiles, seeds and console buffers stay on the host, so a rebuild cannot
reach them.

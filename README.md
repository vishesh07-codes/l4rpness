# l4rpness

A local harness for black-box pentest campaigns. You point it at a target you are allowed to test, give it scope and context, and it keeps testing on its own.

Current state: dashboard, campaigns, campaign chat, and the agent can run tools. It executes shell commands and reads and writes files in a per-campaign work directory. Everything runs in Docker on your machine.

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

## Container tools

The image ships a small network toolkit so bash has something to work with: curl, wget, jq, dnsutils, nmap, netcat-openbsd, whois, iputils-ping and traceroute. The list lives in `tools.txt`, one line, comma separated. Edit it and rebuild:

    docker compose up --build -d

For a slim image without them:

    L4RP_TOOLSET=minimal docker compose up --build -d

The container runs as root **inside** the container: the agent can install packages at runtime (`apt-get`, `pip`, `npm`) and raw-socket tools work (`nmap -sS`, `nmap -O`, `tcpdump`). `sudo` is installed and passwordless.

Root is kept contained by the container boundary. `compose.yaml` pins `user: "0:0"`, drops all capabilities and adds back only what tooling needs (no `SYS_ADMIN`, no `SYS_MODULE`), sets `no-new-privileges`, keeps Docker's default seccomp profile, and mounts neither the Docker socket nor host paths beyond the project's config files. Private pid, network and mount namespaces mean root inside cannot see or reach the host. For an extra layer against kernel exploits, run the daemon with `userns-remap` or use rootless Docker (both are daemon-wide settings).

## Development

    uv venv && uv pip install -r requirements.txt -r requirements-dev.txt
    L4RP_RELOAD=1 .venv/bin/python -m app

    cd web && npm install && npm run dev

The Vite dev server proxies /api to port 4517.

## Tests

    .venv/bin/pytest

51 tests cover the API, the config loader, the tools, skills and the agent loop, all offline. The DeepSeek call is stubbed.

## Roadmap

- [x] dashboard, campaigns, campaign chat
- [x] shell and file tools in the agent loop
- [ ] http_request tool with response capture
- [ ] campaign runner with supervisors that never stop and workers per task
- [ ] coverage map and the scheduler that picks work from it
- [ ] verifier that replays findings
- [x] skills (SKILL.md, loaded on demand, Skills page)
- [ ] browser tool, MCP servers
- [ ] the boring 24/7 parts: watchdog, resume after crash, log rotation

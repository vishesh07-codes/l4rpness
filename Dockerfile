FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# uv is a static binary (no python, no pip) — copy it in so the build never has
# to download a resolver. It installs the exact same wheels pip would, but
# fetches many in parallel, which is what defeats the per-connection throttle
# some ISPs apply to files.pythonhosted.org (70 MB semgrep wheel: 16 min with
# pip, seconds with uv).
FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.13-slim
COPY --from=uv /uv /uvx /usr/local/bin/
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_BREAK_SYSTEM_PACKAGES=1 PIP_ROOT_USER_ACTION=ignore
# uv reads these; --system targets the image's python (no venv in the container).
ENV UV_SYSTEM_PYTHON=1 UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    UV_EXTRA_INDEX_URL=https://pypi.org/simple/ UV_INDEX_STRATEGY=unsafe-best-match
WORKDIR /app

# Some ISPs (Jio/India among them) throttle the IPv4 route to the Fastly edge
# behind deb.debian.org down to ~90 KB/s while the same host is fine over
# IPv6 or from other mirrors. Build containers are IPv4-only, so apt crawls.
# Point apt at a mirror that peers well and keep retries/timeouts sane. This
# applies to every apt call below, including the one `playwright
# install --with-deps` runs for Chromium's system libraries.
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's|deb.debian.org|mirror.init7.net|g' /etc/apt/sources.list.d/debian.sources; \
    elif [ -f /etc/apt/sources.list ]; then \
      sed -i 's|deb.debian.org|mirror.init7.net|g' /etc/apt/sources.list; \
    fi; \
    printf '%s\n' \
      'Acquire::Retries "5";' \
      'Acquire::http::Timeout "30";' \
      'Acquire::https::Timeout "30";' \
      > /etc/apt/apt.conf.d/99-pentest-net

RUN useradd -m -u 1000 app

# passwordless sudo as a fallback for tools that insist on it; the container
# itself runs as root (see the USER directive at the bottom)
RUN apt-get update && \
    apt-get install -y --no-install-recommends sudo && \
    rm -rf /var/lib/apt/lists/* && \
    echo 'app ALL=(ALL:ALL) NOPASSWD:ALL' > /etc/sudoers.d/app && \
    chmod 0440 /etc/sudoers.d/app && \
    visudo -cf /etc/sudoers.d/app
COPY requirements.txt .
# pip's default index (files.pythonhosted.org) sits on the same Fastly edge that
# gets throttled on some ISPs (~70 KB/s here vs ~4 MB/s from a mirror). uv reads
# UV_INDEX_URL (set above) and pulls wheels in parallel. /etc/pip.conf stays for
# runtime `pip install` (agents do this) so those keep working too.
RUN printf '%s\n' \
      '[global]' \
      'index-url = https://mirrors.aliyun.com/pypi/simple/' \
      'extra-index-url = https://pypi.org/simple/' \
      'timeout = 60' \
      'retries = 5' \
      > /etc/pip.conf && \
    uv pip install -r requirements.txt

ARG TOOLSET=custom
COPY tools.txt /tmp/tools.txt
RUN if [ "$TOOLSET" = "custom" ]; then \
      echo "installing tools: $(cat /tmp/tools.txt)" && \
      apt-get update && \
      apt-get install -y --no-install-recommends $(tr ',' ' ' < /tmp/tools.txt) && \
      rm -rf /var/lib/apt/lists/*; \
    else \
      echo "minimal image, no extra tools"; \
    fi && rm -f /tmp/tools.txt

RUN if [ "$TOOLSET" = "custom" ]; then \
      echo "installing mcp tool binaries" && \
      uv pip install semgrep pip-audit && \
      apt-get update && \
      apt-get install -y --no-install-recommends nodejs npm && \
      rm -rf /var/lib/apt/lists/* && \
      python -c "import io, tarfile, urllib.request; d = urllib.request.urlopen('https://github.com/gitleaks/gitleaks/releases/download/v8.24.3/gitleaks_8.24.3_linux_x64.tar.gz').read(); tarfile.open(fileobj=io.BytesIO(d)).extract('gitleaks', '/usr/local/bin', filter='data')" && \
      chmod +x /usr/local/bin/gitleaks; \
    else \
      echo "minimal image, no mcp tool binaries"; \
    fi

# Headless Chromium behind the Playwright MCP server: the agent drives it
# through the playwright_* tools (see app/mcp.py). Its own layer so browser
# downloads survive edits to the tool block above.
RUN if [ "$TOOLSET" = "custom" ]; then \
      echo "installing the playwright browser server" && \
      npm install -g @playwright/mcp@0.0.83 && \
      cd /usr/local/lib/node_modules/@playwright/mcp && \
      npx playwright install --with-deps chromium; \
    else \
      echo "minimal image, no playwright browser"; \
    fi

COPY vendor/frank/ /frank/
COPY vendor/specter/ /opt/specter/
COPY docker/specter /usr/local/bin/specter
RUN chmod +x /usr/local/bin/specter && \
    mkdir -p /home/cran/Documents/pentest/scripts/search_method && \
    chown -R app:app /home/cran

COPY app/ ./app/
COPY prompts/ ./prompts/
COPY skills/ ./skills/
COPY config.toml ./config.toml
COPY AGENTS.md ./AGENTS.md
COPY --from=web /web/dist ./web/dist
RUN mkdir -p /app/data && chown -R app:app /app/data

# Root inside the container: agents install packages at runtime and raw-socket
# tooling (nmap -sS/-O, tcpdump) needs root. Escape hardening lives in
# compose.yaml (cap_drop ALL + minimal caps, no-new-privileges, default seccomp,
# no docker socket, no privileged mode) so this root stays inside the container.
USER root
EXPOSE 4517

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4517/api/health')" || exit 1

CMD ["python", "-m", "app"]

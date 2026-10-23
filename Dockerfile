FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN useradd -m -u 1000 app
COPY requirements.txt .
RUN pip install -r requirements.txt

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

COPY app/ ./app/
COPY prompts/ ./prompts/
COPY skills/ ./skills/
COPY config.toml ./config.toml
COPY AGENTS.md ./AGENTS.md
COPY --from=web /web/dist ./web/dist
RUN mkdir -p /app/data && chown -R app:app /app/data

USER app
EXPOSE 4517

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4517/api/health')" || exit 1

CMD ["python", "-m", "app"]

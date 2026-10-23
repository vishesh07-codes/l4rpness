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

COPY app/ ./app/
COPY prompts/ ./prompts/
COPY config.toml ./config.toml
COPY --from=web /web/dist ./web/dist
RUN mkdir -p /app/data && chown -R app:app /app/data

USER app
EXPOSE 4517

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4517/api/health')" || exit 1

CMD ["python", "-m", "app"]

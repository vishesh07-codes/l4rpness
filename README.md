# l4rpness

Local harness for black-box pentest campaigns. Dashboard, campaign setup, and a per-campaign agent chat. The agent has no tools yet — it can only talk.

## Run

    cp .env.example .env
    # put your DeepSeek key in .env
    docker compose up --build -d

Dashboard: http://127.0.0.1:4517

## Data

Campaigns, messages and uploaded files live in the `l4rpness-data` volume (`/app/data`).

    docker compose down -v     # wipe everything

## Dev

    uv venv && uv pip install -r requirements.txt -r requirements-dev.txt
    .venv/bin/uvicorn app.main:app --reload

    cd web && npm install && npm run dev    # http://localhost:5173, proxies /api

## Tests

    .venv/bin/pytest

import asyncio
import json
import logging
import shutil

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import agent, config, db, uploads

log = logging.getLogger("l4rpness")
router = APIRouter(prefix="/api")

RUNNING: dict[int, dict] = {}


def row_dict(row):
    return {key: row[key] for key in row.keys()}


def load_campaign(conn, campaign_id):
    row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not row:
        raise HTTPException(404, "campaign not found")
    return row


@router.get("/health")
def health():
    return {"status": "ok", "agent_configured": bool(config.API_KEY)}


@router.get("/config")
async def app_config():
    models = await agent.models()
    if config.DEFAULT_MODEL not in models:
        models = [config.DEFAULT_MODEL, *models]

    return {
        "models": models,
        "default_model": config.DEFAULT_MODEL,
        "efforts": config.EFFORTS,
        "default_effort": config.DEFAULT_EFFORT,
        "agent_configured": bool(config.API_KEY),
    }


@router.get("/campaigns")
def list_campaigns():
    with db.db() as conn:
        rows = conn.execute(
            """
            SELECT c.*, COUNT(m.id) AS message_count
            FROM campaigns c
            LEFT JOIN messages m ON m.campaign_id = c.id
            GROUP BY c.id
            ORDER BY c.id DESC
            """
        ).fetchall()
    return [{**row_dict(r), "running": r["id"] in RUNNING} for r in rows]


@router.get("/usage")
def usage(days: int = 7):
    window = 30 if days > 7 else 7
    with db.db() as conn:
        rows = conn.execute(
            """
            SELECT date(m.created_at) AS date,
                   c.model AS model,
                   c.target AS target,
                   SUM(m.input_tokens) AS input_tokens,
                   SUM(m.cached_tokens) AS cached_tokens,
                   SUM(m.output_tokens) AS output_tokens,
                   SUM(m.requests) AS requests,
                   SUM(m.cost_usd) AS cost_usd
            FROM messages m
            JOIN campaigns c ON c.id = m.campaign_id
            WHERE m.role = 'assistant'
              AND m.created_at >= date('now', ?)
            GROUP BY date(m.created_at), c.model, c.target
            ORDER BY date(m.created_at)
            """,
            (f"-{window - 1} days",),
        ).fetchall()

    return [
        {
            "date": row["date"],
            "provider": "deepseek",
            "model": row["model"],
            "agent": (row["target"] or "").split("\n")[0][:60] or "l4rpness",
            "inputTokens": row["input_tokens"] or 0,
            "cachedInputTokens": row["cached_tokens"] or 0,
            "outputTokens": row["output_tokens"] or 0,
            "requests": row["requests"] or 0,
            "costUsd": row["cost_usd"] or 0,
        }
        for row in rows
    ]


@router.post("/campaigns", status_code=201)
async def create_campaign(
    target: str = Form(""),
    model: str = Form(""),
    effort: str = Form(""),
    info: str = Form(""),
    instructions: str = Form(""),
    description: str = Form(""),
    scope: str = Form(""),
    target_files: list[UploadFile] = File(default=[]),
    info_files: list[UploadFile] = File(default=[]),
    instructions_files: list[UploadFile] = File(default=[]),
    description_files: list[UploadFile] = File(default=[]),
    scope_files: list[UploadFile] = File(default=[]),
):
    if not target.strip():
        raise HTTPException(400, "target is required")
    if model not in await agent.models():
        model = config.DEFAULT_MODEL
    if effort not in config.EFFORTS:
        effort = config.DEFAULT_EFFORT

    uploads_by_field = {
        "target": target_files,
        "info": info_files,
        "instructions": instructions_files,
        "description": description_files,
        "scope": scope_files,
    }
    collected = {field: await uploads.collect(files) for field, files in uploads_by_field.items()}

    campaign_id = None
    try:
        with db.db() as conn:
            cur = conn.execute(
                """
                INSERT INTO campaigns (target, info, instructions, description, scope, model, effort)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (target.strip(), info.strip(), instructions.strip(), description.strip(), scope.strip(), model, effort),
            )
            campaign_id = cur.lastrowid
            if campaign_id is None:
                raise HTTPException(500, "failed to create campaign")
            for field, items in collected.items():
                for item in items:
                    path, name = uploads.store(campaign_id, field, item)
                    conn.execute(
                        """
                        INSERT INTO attachments (campaign_id, field, filename, content_type, size, path)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (campaign_id, field, name, item["content_type"], item["size"], path),
                    )
    except OSError:
        if campaign_id:
            shutil.rmtree(config.UPLOAD_DIR / str(campaign_id), ignore_errors=True)
        raise HTTPException(500, "failed to store uploads")

    return get_campaign(campaign_id)


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: int):
    with db.db() as conn:
        campaign = load_campaign(conn, campaign_id)
        attachments = conn.execute(
            "SELECT * FROM attachments WHERE campaign_id = ? ORDER BY field, id",
            (campaign_id,),
        ).fetchall()
    return {**row_dict(campaign), "running": campaign_id in RUNNING, "attachments": [row_dict(a) for a in attachments]}


@router.delete("/campaigns/{campaign_id}")
def delete_campaign(campaign_id: int):
    run = RUNNING.get(campaign_id)
    if run is not None:
        run["stop"].set()

    with db.db() as conn:
        load_campaign(conn, campaign_id)
        conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))

    shutil.rmtree(config.UPLOAD_DIR / str(campaign_id), ignore_errors=True)
    shutil.rmtree(config.WORK_DIR / str(campaign_id), ignore_errors=True)
    return {"deleted": campaign_id}


@router.post("/campaigns/{campaign_id}/stop")
def stop_campaign(campaign_id: int):
    with db.db() as conn:
        load_campaign(conn, campaign_id)

    run = RUNNING.get(campaign_id)
    if run is None:
        return {"stopped": False}

    run["stop"].set()
    return {"stopped": True}


@router.get("/campaigns/{campaign_id}/live")
def live_campaign(campaign_id: int):
    with db.db() as conn:
        load_campaign(conn, campaign_id)

    run = RUNNING.get(campaign_id)
    if run is None:
        return {"running": False, "parts": [], "pending": None}
    return {"running": True, "parts": run["parts"], "pending": run["pending"]}


class MessageIn(BaseModel):
    content: str


@router.get("/campaigns/{campaign_id}/messages")
def list_messages(campaign_id: int):
    with db.db() as conn:
        load_campaign(conn, campaign_id)
        rows = conn.execute(
            "SELECT * FROM messages WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        ).fetchall()
    return [row_dict(r) for r in rows]


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/campaigns/{campaign_id}/messages")
async def send_message(campaign_id: int, body: MessageIn):
    content = body.content.strip()
    if not content:
        raise HTTPException(400, "message is empty")
    if not config.API_KEY:
        raise HTTPException(400, "DEEPSEEK_API_KEY is not set")
    if campaign_id in RUNNING:
        raise HTTPException(409, "campaign is already running")

    with db.db() as conn:
        campaign = load_campaign(conn, campaign_id)
        attachments = conn.execute(
            "SELECT * FROM attachments WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchall()
        prior = conn.execute(
            "SELECT role, content FROM messages WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        ).fetchall()

    history = [{"role": r["role"], "content": r["content"]} for r in prior]
    history.append({"role": "user", "content": content})

    stop = asyncio.Event()
    parts: list = []
    RUNNING[campaign_id] = {"stop": stop, "parts": parts, "pending": None}

    async def stream():
        thinking = []
        answer = []
        calls = []
        failure = ""
        stopped = False
        usage = {"input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "requests": 0}

        def add_text(kind: str, text: str):
            if parts and parts[-1]["type"] == kind:
                parts[-1]["text"] += text
            else:
                parts.append({"type": kind, "text": text})

        try:
            async for kind, data in agent.run(campaign, attachments, history, campaign_id, stop, usage):
                if kind == "thinking":
                    thinking.append(data)
                    add_text("thinking", data)
                    yield sse("thinking", {"text": data})
                elif kind == "content":
                    answer.append(data)
                    add_text("content", data)
                    yield sse("content", {"text": data})
                elif kind == "tool_start":
                    RUNNING[campaign_id]["pending"] = data["name"]
                    yield sse("tool_start", data)
                elif kind == "tool_call":
                    calls.append({**data, "output": None, "ok": None, "ms": None})
                    parts.append({"type": "tool", **data})
                    RUNNING[campaign_id]["pending"] = None
                    yield sse("tool_call", data)
                elif kind == "stopped":
                    stopped = True
                    break
                elif kind == "tool_result":
                    for call in calls:
                        if call["id"] == data["id"]:
                            call.update(output=data["output"], ok=data["ok"], ms=data["ms"])
                    for part in parts:
                        if part["type"] == "tool" and part["id"] == data["id"]:
                            part.update(output=data["output"], ok=data["ok"], ms=data["ms"])
                    yield sse("tool_result", data)
                else:
                    answer.append(data)
                    add_text("content", data)
                    yield sse("content", {"text": data})
        except Exception:
            log.exception("agent run failed")
            failure = "agent request failed"

        reply = "".join(answer).strip()
        has_output = bool(reply or thinking or calls)

        if has_output and not reply:
            reply = "(no reply text)"

        try:
            with db.db() as conn:
                user_cur = conn.execute(
                    "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'user', ?)",
                    (campaign_id, content),
                )
                assistant_row = None
                if has_output:
                    cost = config.cost_usd(
                        usage["input_tokens"], usage["cached_tokens"], usage["output_tokens"]
                    )
                    assistant_cur = conn.execute(
                        """
                        INSERT INTO messages (
                            campaign_id, role, content, reasoning, tool_calls, parts,
                            input_tokens, cached_tokens, output_tokens, requests, cost_usd
                        )
                        VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            campaign_id,
                            reply,
                            "".join(thinking).strip(),
                            json.dumps(calls),
                            json.dumps(parts),
                            usage["input_tokens"],
                            usage["cached_tokens"],
                            usage["output_tokens"],
                            usage["requests"],
                            cost,
                        ),
                    )
                    assistant_row = conn.execute(
                        "SELECT * FROM messages WHERE id = ?", (assistant_cur.lastrowid,)
                    ).fetchone()
                conn.execute("UPDATE campaigns SET updated_at = datetime('now') WHERE id = ?", (campaign_id,))
                user_row = conn.execute("SELECT * FROM messages WHERE id = ?", (user_cur.lastrowid,)).fetchone()
        except Exception:
            log.exception("storing reply failed")
            yield sse("error", {"detail": "failed to store the reply", "saved": False})
            return

        if failure:
            yield sse("error", {"detail": failure, "saved": True})
            return

        if stopped:
            yield sse("error", {"detail": "stopped by user", "saved": True})
            return

        if not has_output:
            yield sse("error", {"detail": "agent returned an empty reply", "saved": True})
            return

        yield sse("done", {"user": row_dict(user_row), "assistant": row_dict(assistant_row)})

    async def events():
        try:
            async for chunk in stream():
                yield chunk
        finally:
            RUNNING.pop(campaign_id, None)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/attachments/{attachment_id}")
def get_attachment(attachment_id: int):
    with db.db() as conn:
        row = conn.execute("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
    if not row:
        raise HTTPException(404, "attachment not found")

    path = config.UPLOAD_DIR / row["path"]
    if not path.is_file():
        raise HTTPException(404, "attachment file missing")

    return FileResponse(path, filename=row["filename"], media_type=row["content_type"] or "application/octet-stream")

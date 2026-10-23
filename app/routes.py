import asyncio
import json
import logging
import shutil

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import agent, config, db, seed, skills, uploads

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
    default = config.DEFAULT_MODEL
    if models and default not in models:
        default = models[0]
    if default not in models:
        models = [default, *models]

    return {
        "models": models,
        "default_model": default,
        "provider": config.PROVIDER,
        "efforts": config.EFFORTS,
        "default_effort": config.DEFAULT_EFFORT,
        "agent_configured": bool(config.API_KEY),
    }


@router.get("/skills")
def list_skills():
    return [
        {
            "name": entry["name"],
            "description": entry["description"],
            "source": "custom" if entry["editable"] else "built-in",
        }
        for entry in skills.discover()
    ]


@router.get("/skills/{name}")
def get_skill(name: str):
    entry = skills.load(name)
    if entry is None:
        raise HTTPException(404, "skill not found")

    files = sorted(
        str(path.relative_to(entry["dir"]))
        for path in entry["dir"].rglob("*")
        if path.is_file() and path.name != "SKILL.md"
    )
    return {
        "name": entry["name"],
        "description": entry["description"],
        "source": "custom" if entry["editable"] else "built-in",
        "body": entry["body"],
        "files": files,
    }


class SkillIn(BaseModel):
    name: str = ""
    description: str
    body: str = ""


@router.post("/skills", status_code=201)
def create_skill(body: SkillIn):
    try:
        return skills.save(body.name, body.description, body.body, overwrite=False)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except FileExistsError:
        raise HTTPException(409, "skill already exists")


@router.put("/skills/{name}")
def update_skill(name: str, body: SkillIn):
    entry = skills.load(name)
    if entry is None:
        raise HTTPException(404, "skill not found")
    if not entry["editable"]:
        raise HTTPException(403, "built-in skills are edited in the repo")

    try:
        return skills.save(name, body.description, body.body, overwrite=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


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

            for message in seed.messages(target.strip()):
                role = str(message.get("role", ""))
                body = str(message.get("content", ""))
                if role == "user":
                    conn.execute(
                        "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'user', ?)",
                        (campaign_id, body),
                    )
                elif role == "assistant":
                    conn.execute(
                        """
                        INSERT INTO messages (campaign_id, role, content, reasoning, tool_calls, parts)
                        VALUES (?, 'assistant', ?, ?, ?, ?)
                        """,
                        (
                            campaign_id,
                            body,
                            str(message.get("reasoning", "")),
                            json.dumps(message.get("tool_calls", [])),
                            json.dumps(message.get("parts", [])),
                        ),
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
        files = conn.execute(
            "SELECT * FROM message_attachments WHERE campaign_id = ? ORDER BY id",
            (campaign_id,),
        ).fetchall()

    by_message: dict[int, list] = {}
    for file in files:
        by_message.setdefault(file["message_id"], []).append(row_dict(file))

    return [
        {**row_dict(row), "attachments": by_message.get(row["id"], [])} for row in rows
    ]


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _run(campaign_id, campaign, attachments, history, content, run, files=()):
    """The agent turn, detached from any client so closing the tab does not kill it."""
    stop = run["stop"]
    parts = run["parts"]
    thinking: list = []
    answer: list = []
    calls: list = []
    failure = ""
    stopped = False

    def add_text(kind: str, text: str):
        if parts and parts[-1]["type"] == kind:
            parts[-1]["text"] += text
        else:
            parts.append({"type": kind, "text": text})

    def emit(name: str, data):
        # only buffer events when a client is actually watching
        if run["consumer"]:
            run["queue"].put_nowait((name, data))

    try:
        async for kind, data in agent.run(campaign, attachments, history, campaign_id, stop):
            if kind == "thinking":
                thinking.append(data)
                add_text("thinking", data)
                emit("thinking", {"text": data})
            elif kind == "content":
                answer.append(data)
                add_text("content", data)
                emit("content", {"text": data})
            elif kind == "tool_start":
                run["pending"] = data["name"]
                emit("tool_start", data)
            elif kind == "tool_call":
                calls.append({**data, "output": None, "ok": None, "ms": None})
                parts.append({"type": "tool", **data})
                run["pending"] = None
                emit("tool_call", data)
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
                emit("tool_result", data)
            else:
                answer.append(data)
                add_text("content", data)
                emit("content", {"text": data})
    except Exception:
        log.exception("agent run failed")
        failure = "agent request failed"

    reply = "".join(answer).strip()
    has_output = bool(reply or thinking or calls)
    if has_output and not reply:
        reply = "(no reply text)"

    user_row = assistant_row = None
    user_attachments: list = []
    stored = True
    try:
        with db.db() as conn:
            user_cur = conn.execute(
                "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'user', ?)",
                (campaign_id, content),
            )
            user_id = user_cur.lastrowid
            for item in files:
                conn.execute(
                    """
                    INSERT INTO message_attachments
                        (campaign_id, message_id, filename, content_type, size, path)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        campaign_id,
                        user_id,
                        item["filename"],
                        item["content_type"],
                        item["size"],
                        item["path"],
                    ),
                )
            if has_output:
                assistant_cur = conn.execute(
                    """
                    INSERT INTO messages (campaign_id, role, content, reasoning, tool_calls, parts)
                    VALUES (?, 'assistant', ?, ?, ?, ?)
                    """,
                    (
                        campaign_id,
                        reply,
                        "".join(thinking).strip(),
                        json.dumps(calls),
                        json.dumps(parts),
                    ),
                )
                assistant_row = conn.execute(
                    "SELECT * FROM messages WHERE id = ?", (assistant_cur.lastrowid,)
                ).fetchone()
            conn.execute("UPDATE campaigns SET updated_at = datetime('now') WHERE id = ?", (campaign_id,))
            user_row = conn.execute(
                "SELECT * FROM messages WHERE id = ?", (user_id,)
            ).fetchone()
            user_attachments = [
                row_dict(a)
                for a in conn.execute(
                    "SELECT * FROM message_attachments WHERE message_id = ? ORDER BY id",
                    (user_id,),
                ).fetchall()
            ]
    except Exception:
        log.exception("storing reply failed")
        stored = False

    RUNNING.pop(campaign_id, None)

    if not stored:
        emit("error", {"detail": "failed to store the reply", "saved": False})
    elif failure:
        emit("error", {"detail": failure, "saved": True})
    elif stopped:
        emit("error", {"detail": "stopped by user", "saved": True})
    elif not has_output:
        emit("error", {"detail": "agent returned an empty reply", "saved": True})
    else:
        user_dict = row_dict(user_row)
        user_dict["attachments"] = user_attachments
        emit("done", {"user": user_dict, "assistant": row_dict(assistant_row)})
    emit("__end__", None)


@router.post("/campaigns/{campaign_id}/messages")
async def send_message(
    campaign_id: int,
    content: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    text = content.strip()
    items = await uploads.collect(files) if files else []
    if not text and not items:
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

    stored = []
    for item in items:
        rel = uploads.store_chat(campaign_id, item)
        stored.append(
            {
                "filename": item["filename"],
                "content_type": item["content_type"],
                "size": item["size"],
                "path": rel,
            }
        )

    history = [{"role": r["role"], "content": r["content"]} for r in prior]
    prompt = text
    if stored:
        context = uploads.prompt_text(stored)
        prompt = f"{text}\n\n{context}".strip() if text else context
    history.append({"role": "user", "content": prompt})

    run = {
        "stop": asyncio.Event(),
        "parts": [],
        "pending": None,
        "queue": asyncio.Queue(),
        "consumer": False,
    }
    RUNNING[campaign_id] = run

    async def events():
        run["consumer"] = True
        # start the run only once someone is watching; from here it is detached
        # from the request, so closing the tab lets it finish in the background
        run["task"] = asyncio.create_task(
            _run(campaign_id, campaign, attachments, history, text, run, stored)
        )
        try:
            while True:
                name, data = await run["queue"].get()
                if name == "__end__":
                    break
                yield sse(name, data)
        finally:
            run["consumer"] = False

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

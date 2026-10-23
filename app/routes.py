import logging
import shutil

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import agent, config, db, uploads

log = logging.getLogger("l4rpness")
router = APIRouter(prefix="/api")


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
def app_config():
    return {
        "models": config.MODELS,
        "default_model": config.DEFAULT_MODEL,
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
    return [row_dict(r) for r in rows]


@router.post("/campaigns", status_code=201)
async def create_campaign(
    target: str = Form(""),
    model: str = Form(""),
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
    if model not in config.MODELS:
        model = config.DEFAULT_MODEL

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
                INSERT INTO campaigns (target, info, instructions, description, scope, model)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (target.strip(), info.strip(), instructions.strip(), description.strip(), scope.strip(), model),
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
    return {**row_dict(campaign), "attachments": [row_dict(a) for a in attachments]}


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


@router.post("/campaigns/{campaign_id}/messages", status_code=201)
async def send_message(campaign_id: int, body: MessageIn):
    content = body.content.strip()
    if not content:
        raise HTTPException(400, "message is empty")
    if not config.API_KEY:
        raise HTTPException(400, "DEEPSEEK_API_KEY is not set")

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

    try:
        answer = await agent.reply(campaign, attachments, history)
    except Exception:
        log.exception("agent call failed")
        raise HTTPException(502, "agent request failed")

    with db.db() as conn:
        user_cur = conn.execute(
            "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'user', ?)",
            (campaign_id, content),
        )
        assistant_cur = conn.execute(
            "INSERT INTO messages (campaign_id, role, content) VALUES (?, 'assistant', ?)",
            (campaign_id, answer),
        )
        conn.execute("UPDATE campaigns SET updated_at = datetime('now') WHERE id = ?", (campaign_id,))
        user_row = conn.execute("SELECT * FROM messages WHERE id = ?", (user_cur.lastrowid,)).fetchone()
        assistant_row = conn.execute("SELECT * FROM messages WHERE id = ?", (assistant_cur.lastrowid,)).fetchone()

    return {"user": row_dict(user_row), "assistant": row_dict(assistant_row)}


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

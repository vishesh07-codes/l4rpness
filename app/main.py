import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from . import config, db, mcp, supervisor
from .routes import router

# the app logs to "l4rpness"; without a handler only warnings+ show (python's
# last-resort handler), so bring INFO up on the console
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
log = logging.getLogger("l4rpness")


async def _watch_host_env() -> None:
    """Apply outside edits to the mounted host `.env` within seconds, so hand
    edits take effect without a restart or a button press. Host wins over
    conflicting Settings overrides (single source of truth)."""
    from . import agent, settings

    while True:
        await asyncio.sleep(5)
        try:
            if not settings.host_state()["changed"]:
                continue
            changed = settings.reload_host_env()
        except Exception as exc:
            log.warning("l4rpness: host .env watch failed: %s", exc)
            continue
        if changed:
            agent._models.clear()
            settings.note_auto_applied(changed)
            log.info("auto-applied host .env changes: %s", ", ".join(changed))


@asynccontextmanager
async def lifespan(_app):
    db.init()
    db.recover()
    await mcp.start()
    supervisor.restore()
    watcher = asyncio.create_task(_watch_host_env())
    try:
        yield
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
        await mcp.stop()


app = FastAPI(title="l4rpness", lifespan=lifespan)
# hashed assets compress ~3x; text/event-stream is excluded upstream, so chat
# streaming is untouched
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.include_router(router)


class CachedStaticFiles(StaticFiles):
    """Hashed filenames never change, so cache them forever."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


if config.WEB_DIST.is_dir():
    app.mount(
        "/assets",
        CachedStaticFiles(directory=config.WEB_DIST / "assets"),
        name="assets",
    )


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "not found")

    index = config.WEB_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(404, "web build not found")

    candidate = (config.WEB_DIST / path).resolve()
    if path and candidate.is_file() and candidate.is_relative_to(config.WEB_DIST.resolve()):
        return FileResponse(candidate)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})

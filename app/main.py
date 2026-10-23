from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .routes import router


@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


app = FastAPI(title="l4rpness", lifespan=lifespan)
app.include_router(router)

if config.WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=config.WEB_DIST / "assets"), name="assets")


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
    return FileResponse(index)

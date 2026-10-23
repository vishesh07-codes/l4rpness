import os

import uvicorn

from . import config

uvicorn.run(
    "app.main:app",
    host=config.SERVER_HOST,
    port=config.SERVER_PORT,
    reload=bool(os.getenv("L4RP_RELOAD")),
    # only watch code, never data/: agent work dirs can contain symlink loops
    # (e.g. builds) that crash the reload watcher
    reload_dirs=[str(config.ROOT / "app"), str(config.ROOT / "prompts")],
)

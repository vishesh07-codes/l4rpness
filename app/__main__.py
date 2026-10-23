import os

import uvicorn

from . import config

uvicorn.run(
    "app.main:app",
    host=config.SERVER_HOST,
    port=config.SERVER_PORT,
    reload=bool(os.getenv("L4RP_RELOAD")),
)

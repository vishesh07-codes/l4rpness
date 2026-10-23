import json
import logging
from typing import Any

from . import config

log = logging.getLogger("l4rpness")


def _substitute(value: Any, target: str) -> Any:
    if isinstance(value, str):
        return value.replace("{{target}}", target)
    if isinstance(value, list):
        return [_substitute(item, target) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item, target) for key, item in value.items()}
    return value


def messages(target: str) -> list[dict]:
    if config.SEED_HISTORY is None:
        return []

    try:
        raw = json.loads(config.SEED_HISTORY.read_text())
    except OSError:
        return []
    except json.JSONDecodeError:
        log.warning("seed history is not valid json: %s", config.SEED_HISTORY)
        return []

    if not isinstance(raw, list):
        log.warning("seed history must be a list of messages: %s", config.SEED_HISTORY)
        return []

    return [_substitute(message, target) for message in raw if isinstance(message, dict)]

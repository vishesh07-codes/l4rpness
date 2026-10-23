"""Provider API-key management with UI <-> env sync.

Read path (env -> UI): `status()` resolves every provider's key var from the
live process env and reports whether it is set, where it came from (the host
`.env`/environment vs the Settings UI overlay), and a masked hint.

Write path (UI -> env): `save()` persists the keys to the overlay file in the
data volume, re-applies it to the process env via `config.refresh_env()`, and
the caller clears the cached model list — so saved keys take effect without a
restart and survive container recreates.

The container cannot rewrite the host `.env` (it only receives its values via
compose `env_file` at creation), so the overlay is the writable store; values
already present in the host `.env` keep working untouched and are reported as
`environment` source.
"""

import logging
import os
import re
import time

from dotenv import dotenv_values

from . import config

log = logging.getLogger("l4rpness")

_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# content hash of the host `.env` as last seen by us (startup, reload, or
# push); `host_state()` compares against it to flag outside edits.
_HOST_SIG: str | None = config.host_env_signature()

# last watcher-applied change, so the UI can announce it
_LAST_AUTO_APPLIED: dict = {"vars": [], "at": None}


def allowed_vars() -> set[str]:
    return {
        str(conf.get("api_key_env", ""))
        for conf in config.PROVIDERS.values()
        if conf.get("api_key_env")
    }


def status() -> list[dict]:
    overlay = config.read_env_overlay()
    out = []
    for provider, conf in config.PROVIDERS.items():
        var = str(conf.get("api_key_env", ""))
        if not var:
            continue
        value = os.getenv(var, "").strip()
        source = "settings" if var in overlay else ("environment" if value else "missing")
        out.append(
            {
                "provider": provider,
                "env_var": var,
                "configured": bool(value),
                "source": source,
                "length": len(value) if value else 0,
                # just enough to recognise the key, never the secret itself
                "suffix": value[-4:] if len(value) >= 8 else None,
            }
        )
    return out


def _write_env_overlay(current: dict[str, str]) -> None:
    path = config.DATA_DIR / config.ENV_OVERLAY_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Managed by the Settings UI. Provider keys saved here win over process env.",
        "",
    ]
    for name in sorted(current):
        lines.append(f"{name}={current[name]}")
    path.write_text("\n".join(lines) + "\n")
    # secrets at rest: readable only by the owner
    os.chmod(path, 0o600)


def save(values: dict[str, str]) -> list[dict]:
    """Store UI-managed keys; an empty value clears that var's override."""
    allowed = allowed_vars()
    current = config.read_env_overlay()
    for name, value in (values or {}).items():
        if name not in allowed or not _VAR_RE.match(name):
            raise ValueError(f"refusing to store {name!r}")
        value = (value or "").strip()
        if value:
            current[name] = value
        else:
            current.pop(name, None)

    _write_env_overlay(current)
    config.refresh_env()
    log.info("provider keys updated via settings: %s", sorted(values or {}))
    return status()


# Runtime config knobs (config.toml values that are safe to change live).
# `key` is a dotted path into the settings dict; server binding, storage
# paths, and provider wiring are deliberately absent (they need a recreate).
RUNTIME_SECTIONS = [
    {"id": "model", "title": "Model defaults"},
    {"id": "requests", "title": "Requests"},
    {"id": "supervisor", "title": "Supervisor"},
    {"id": "generators", "title": "Generators"},
    {"id": "tools", "title": "Tool limits"},
    {"id": "uploads", "title": "Uploads"},
]

RUNTIME_FIELDS = [
    {
        "key": "llm.provider",
        "section": "model",
        "label": "Default provider",
        "hint": "Used when a model name has no provider prefix.",
        "kind": "select",
        "options_from": "providers",
    },
    {
        "key": "llm.model",
        "section": "model",
        "label": "Default model",
        "hint": "Used for new campaigns.",
        "kind": "text",
    },
    {
        "key": "llm.effort",
        "section": "model",
        "label": "Default effort",
        "hint": "",
        "kind": "select",
        "options_from": "efforts",
    },
    {
        "key": "llm.efforts",
        "section": "model",
        "label": "Effort levels",
        "hint": "Comma-separated.",
        "kind": "list",
    },
    {
        "key": "llm.request_timeout",
        "section": "requests",
        "label": "Request timeout (s)",
        "hint": "",
        "kind": "int",
        "min": 10,
        "max": 1800,
    },
    {
        "key": "llm.temperature",
        "section": "requests",
        "label": "Temperature",
        "hint": "Ignored while thinking mode is on.",
        "kind": "number",
        "min": 0,
        "max": 2,
        "step": 0.1,
    },
    {
        "key": "supervisor.error_backoff_seconds",
        "section": "supervisor",
        "label": "Error backoff (s)",
        "hint": "Pause before retrying a failed chain.",
        "kind": "int",
        "min": 0,
        "max": 600,
    },
    {
        "key": "supervisor.compact_at_tokens",
        "section": "supervisor",
        "label": "Compact above (tokens)",
        "hint": "0 disables compaction.",
        "kind": "int",
        "min": 0,
        "max": 2000000,
    },
    {
        "key": "supervisor.compact_keep_messages",
        "section": "supervisor",
        "label": "Compact keep (messages)",
        "hint": "",
        "kind": "int",
        "min": 1,
        "max": 50,
    },
    {
        "key": "hypotheses.max_generators",
        "section": "generators",
        "label": "Max parallel generators",
        "hint": "",
        "kind": "int",
        "min": 1,
        "max": 20,
    },
    {
        "key": "tools.max_steps",
        "section": "tools",
        "label": "Max tool steps",
        "hint": "-1 means no cap.",
        "kind": "int",
        "min": -1,
        "max": 500,
    },
    {
        "key": "tools.bash_timeout",
        "section": "tools",
        "label": "Bash timeout (s)",
        "hint": "",
        "kind": "int",
        "min": 5,
        "max": 1800,
    },
    {
        "key": "tools.http_timeout",
        "section": "tools",
        "label": "HTTP timeout (s)",
        "hint": "",
        "kind": "int",
        "min": 5,
        "max": 600,
    },
    {
        "key": "tools.output_chars",
        "section": "tools",
        "label": "Output cap (chars)",
        "hint": "",
        "kind": "int",
        "min": 1000,
        "max": 200000,
    },
    {
        "key": "tools.bash_output_bytes",
        "section": "tools",
        "label": "Bash output cap (bytes)",
        "hint": "",
        "kind": "int",
        "min": 1024,
        "max": 100 * 1024 * 1024,
    },
    {
        "key": "tools.read_max_lines",
        "section": "tools",
        "label": "Read cap (lines)",
        "hint": "",
        "kind": "int",
        "min": 100,
        "max": 20000,
    },
    {
        "key": "uploads.max_bytes",
        "section": "uploads",
        "label": "Max upload size (bytes)",
        "hint": "",
        "kind": "int",
        "min": 1024,
        "max": 1024 * 1024 * 1024,
    },
    {
        "key": "uploads.max_files_per_field",
        "section": "uploads",
        "label": "Max files per field",
        "hint": "",
        "kind": "int",
        "min": 1,
        "max": 20,
    },
    {
        "key": "uploads.file_char_cap",
        "section": "uploads",
        "label": "Chars read per file",
        "hint": "",
        "kind": "int",
        "min": 1000,
        "max": 1000000,
    },
    {
        "key": "uploads.total_char_cap",
        "section": "uploads",
        "label": "Chars read per batch",
        "hint": "",
        "kind": "int",
        "min": 1000,
        "max": 5000000,
    },
]


def _field_options(field: dict, efforts: list[str] | None = None) -> list[str]:
    if field.get("options"):
        return list(field["options"])
    if field.get("options_from") == "providers":
        return list(config.PROVIDERS)
    if field.get("options_from") == "efforts":
        return list(efforts if efforts is not None else config.EFFORTS)
    return []


def _dotted(values: dict, key: str):
    section, _, name = key.partition(".")
    group = values.get(section)
    if not isinstance(group, dict):
        return None
    return group.get(name)


def runtime_status() -> dict:
    overlay = config.read_settings_overlay()
    fields = []
    for field in RUNTIME_FIELDS:
        entry = {k: v for k, v in field.items() if k != "options_from"}
        entry["options"] = _field_options(field)
        fields.append(entry)
    values: dict[str, object] = {}
    sources: dict[str, str] = {}
    for field in RUNTIME_FIELDS:
        key = field["key"]
        values[key] = _dotted(config.SETTINGS, key)
        section, _, name = key.partition(".")
        group = overlay.get(section)
        sources[key] = (
            "settings" if isinstance(group, dict) and name in group else "file"
        )
    return {"sections": RUNTIME_SECTIONS, "fields": fields, "values": values, "sources": sources}


def _coerce(field: dict, raw: str, efforts: list[str] | None = None):
    label = field["label"]
    kind = field["kind"]
    text = (raw or "").strip()
    if kind == "text":
        if not text:
            raise ValueError(f"{label} must not be empty.")
        return text
    if kind == "select":
        options = _field_options(field, efforts)
        if text not in options:
            raise ValueError(f"{label} must be one of: {', '.join(options)}.")
        return text
    if kind == "list":
        items = [part.strip() for part in text.split(",") if part.strip()]
        if not items:
            raise ValueError(f"{label} needs at least one entry.")
        return items
    if kind in ("int", "number"):
        try:
            value = int(text) if kind == "int" else float(text)
        except ValueError:
            raise ValueError(f"{label} must be a number.")
        low, high = field.get("min"), field.get("max")
        if (low is not None and value < low) or (high is not None and value > high):
            raise ValueError(f"{label} must be between {low} and {high}.")
        return value
    raise ValueError(f"unknown field {field['key']!r}")


def save_runtime(values: dict[str, str]) -> dict:
    by_key = {field["key"]: field for field in RUNTIME_FIELDS}
    for key in values or {}:
        if key not in by_key:
            raise ValueError(f"refusing to store {key!r}")
    # validate everything before writing anything; a simultaneously-saved
    # efforts list is a valid option pool for the default effort
    pending: dict[str, object] = {}
    efforts: list[str] | None = None
    if values and "llm.efforts" in values:
        efforts = _coerce(by_key["llm.efforts"], values["llm.efforts"])
        pending["llm.efforts"] = efforts
    for key, raw in (values or {}).items():
        if key in pending:
            continue
        pending[key] = _coerce(by_key[key], raw, efforts)
    current = config.read_settings_overlay()
    for key, value in pending.items():
        section, _, name = key.partition(".")
        group = current.setdefault(section, {})
        if not isinstance(group, dict):
            group = current[section] = {}
        group[name] = value
    config.write_settings_overlay(current)
    config.refresh_runtime()
    log.info("runtime config updated via settings: %s", sorted(pending))
    return runtime_status()


def reset_runtime() -> dict:
    """Drop every UI-managed runtime override (config.toml wins again)."""
    path = config.DATA_DIR / config.SETTINGS_OVERLAY_NAME
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    config.refresh_runtime()
    log.info("runtime config overrides reset via settings")
    return runtime_status()


def _mask(value: str) -> dict:
    return {
        "present": bool(value),
        "length": len(value),
        "suffix": value[-4:] if len(value) >= 8 else None,
    }


def host_state() -> dict:
    """What the container can see of the host `.env`: is it mounted and
    writable, has it changed since we last synced, and which managed keys it
    currently holds (masked). Never returns secret values."""
    path = config.host_env_path()
    is_file = path.is_file()
    sig = config.host_env_signature() if is_file else None
    global _HOST_SIG
    if sig and _HOST_SIG is None:
        # first sighting of the file becomes the baseline
        _HOST_SIG = sig
    try:
        readable = bool(is_file and os.access(path, os.R_OK))
        writable = bool(is_file and os.access(path, os.W_OK))
    except OSError:
        readable = False
        writable = False
    info: dict[str, dict] = {}
    if readable and sig:
        try:
            parsed = dotenv_values(path)
        except Exception:
            parsed = {}
        for name in sorted(allowed_vars()):
            raw = parsed.get(name)
            value = str(raw).strip() if isinstance(raw, str) else ""
            info[name] = _mask(value)
    return {
        "mounted": is_file,
        "readable": readable,
        "writable": writable,
        "changed": bool(sig and _HOST_SIG and sig != _HOST_SIG),
        "vars": info,
        # copy: callers must not be able to mutate the module global
        "last_applied": {"vars": list(_LAST_AUTO_APPLIED["vars"]), "at": _LAST_AUTO_APPLIED["at"]},
    }


def note_auto_applied(vars: list[str]) -> None:
    """Record a watcher-applied host change for the UI to announce."""
    global _LAST_AUTO_APPLIED
    _LAST_AUTO_APPLIED = {"vars": list(vars), "at": time.time()}


def _note_host_sig() -> None:
    global _HOST_SIG
    _HOST_SIG = config.host_env_signature()


def reload_host_env() -> list[str]:
    """Pull allowlisted keys from the mounted host `.env` into the process
    env. Host wins: conflicting overlay overrides are pruned so a hand edit
    outside the container actually takes effect. Returns the vars whose
    effective value changed."""
    path = config.host_env_path()
    if not path.is_file():
        return []
    try:
        parsed = dotenv_values(path)
    except Exception:
        return []
    host_values = {
        name: str(raw).strip()
        for name, raw in parsed.items()
        if name in allowed_vars() and isinstance(raw, str) and str(raw).strip()
    }
    changed = []
    for name, value in host_values.items():
        if os.getenv(name, "").strip() != value:
            changed.append(name)
        os.environ[name] = value
        config._ENV_BASELINE[name] = value
    if changed:
        current = config.read_env_overlay()
        pruned = {k: v for k, v in current.items() if k not in host_values}
        if pruned != current:
            _write_env_overlay(pruned)
        config.reload_keys()
        log.info("reloaded host .env via settings: %s", sorted(changed))
    _note_host_sig()
    return sorted(changed)


def _env_quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_@./:+-]+", value):
        return value
    return "'" + value.replace("'", "\\'") + "'"


def _update_env_file(path, updates: dict[str, str]) -> None:
    """Rewrite KEY= lines in place, preserving comments, blank lines, order,
    and unrelated vars; missing keys are appended. In-place (open+truncate)
    is deliberate: the host `.env` is a bind-mounted file, and renames onto
    a mount point fail with EBUSY, so temp-file-and-rename writers
    (e.g. dotenv's set_key) cannot work here."""
    try:
        text = path.read_text()
    except OSError as exc:
        raise ValueError(f"could not read host .env: {exc}")
    pending = dict(updates)
    out = []
    for line in text.splitlines():
        rest = line.strip()
        prefix = ""
        if rest.startswith("export "):
            prefix, rest = "export ", rest[len("export "):].lstrip()
        name, sep, _ = rest.partition("=")
        if sep and name.strip() in pending:
            key = name.strip()
            out.append(f"{prefix}{key}={_env_quote(pending.pop(key))}")
        else:
            out.append(line)
    for key, value in pending.items():
        out.append(f"{key}={_env_quote(value)}")
    try:
        with path.open("w") as fh:
            fh.write("\n".join(out) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        raise ValueError(f"could not write host .env: {exc}")


def push_host_env(values: dict[str, str]) -> list[str]:
    """Write staged keys through to the mounted host `.env` (comments and
    unrelated vars are preserved), then drop their overlay copies so the file
    is the single source of truth again. Empty values never delete host
    lines — clearing only removes the overlay override."""
    path = config.host_env_path()
    if not path.is_file():
        raise ValueError("host .env is not mounted in this container")
    if not os.access(path, os.W_OK):
        raise ValueError("host .env is not writable (check the file permissions)")
    allowed = allowed_vars()
    staged = {}
    for name, value in (values or {}).items():
        if name not in allowed or not _VAR_RE.match(name):
            raise ValueError(f"refusing to store {name!r}")
        value = (value or "").strip()
        if value:
            staged[name] = value
        # clearing never deletes host lines — it only drops the overlay copy
    if staged:
        _update_env_file(path, staged)
        written = sorted(staged)
    else:
        written = []
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    if written:
        current = config.read_env_overlay()
        pruned = {k: v for k, v in current.items() if k not in written}
        if pruned != current:
            _write_env_overlay(pruned)
        # the file is now the source of truth and the value must be live too:
        # push is the only writer that bypasses refresh_env()
        for name in written:
            os.environ[name] = staged[name]
            config._ENV_BASELINE[name] = staged[name]
        log.info("pushed keys to host .env via settings: %s", sorted(written))
    _note_host_sig()
    return sorted(written)

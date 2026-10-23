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

The runtime knobs from `config.toml` follow the same pattern: the overlay is
the base store, and when the host file is mounted and writable,
`push_host_config()` writes values through to it (preserving comments and
unrelated keys) while `reload_host_config()` lets outside edits win over
conflicting overlay copies.
"""

import logging
import os
import re
import time
import tomllib

from dotenv import dotenv_values

from . import config

log = logging.getLogger("l4rpness")

_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# content hash of the host `.env` as last seen by us (startup, reload, or
# push); `host_state()` compares against it to flag outside edits.
_HOST_SIG: str | None = config.host_env_signature()

# last watcher-applied change, so the UI can announce it
_LAST_AUTO_APPLIED: dict = {"vars": [], "at": None}

# same story for the host `config.toml`: change detection and auto-apply for
# the runtime knobs (the .env flow covers provider keys)
_HOST_CONFIG_SIG: str | None = config.host_config_signature()
_LAST_CONFIG_APPLIED: dict = {"keys": [], "at": None}


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


def _coerce_values(values: dict[str, str]) -> dict[str, object]:
    """Validate a batch of raw runtime values against the schema. Raises
    ValueError before anything is written; a simultaneously-saved efforts
    list is a valid option pool for the default effort."""
    by_key = {field["key"]: field for field in RUNTIME_FIELDS}
    for key in values or {}:
        if key not in by_key:
            raise ValueError(f"refusing to store {key!r}")
    pending: dict[str, object] = {}
    efforts: list[str] | None = None
    if values and "llm.efforts" in values:
        efforts = _coerce(by_key["llm.efforts"], values["llm.efforts"])
        pending["llm.efforts"] = efforts
    for key, raw in (values or {}).items():
        if key in pending:
            continue
        pending[key] = _coerce(by_key[key], raw, efforts)
    return pending


def save_runtime(values: dict[str, str]) -> dict:
    pending = _coerce_values(values)
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


def host_config_state() -> dict:
    """What the container can see of the host `config.toml`: is it mounted and
    writable, has it changed since we last synced, and which managed knobs it
    currently defines (values included — config holds no secrets)."""
    path = config.host_config_path()
    is_file = path.is_file()
    sig = config.host_config_signature() if is_file else None
    global _HOST_CONFIG_SIG
    if sig and _HOST_CONFIG_SIG is None:
        # first sighting of the file becomes the baseline
        _HOST_CONFIG_SIG = sig
    try:
        readable = bool(is_file and os.access(path, os.R_OK))
        writable = bool(is_file and os.access(path, os.W_OK))
    except OSError:
        readable = False
        writable = False
    keys: dict[str, object] = {}
    if readable and sig:
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except Exception:
            data = {}
        if isinstance(data, dict):
            for field in RUNTIME_FIELDS:
                section, _, name = field["key"].partition(".")
                group = data.get(section)
                if isinstance(group, dict) and name in group:
                    keys[field["key"]] = group[name]
    return {
        "mounted": is_file,
        "readable": readable,
        "writable": writable,
        "changed": bool(sig and _HOST_CONFIG_SIG and sig != _HOST_CONFIG_SIG),
        "keys": keys,
        # copy: callers must not be able to mutate the module global
        "last_applied": {"keys": list(_LAST_CONFIG_APPLIED["keys"]), "at": _LAST_CONFIG_APPLIED["at"]},
    }


def note_config_auto_applied(keys: list[str]) -> None:
    """Record a watcher-applied host config change for the UI to announce."""
    global _LAST_CONFIG_APPLIED
    _LAST_CONFIG_APPLIED = {"keys": list(keys), "at": time.time()}


def _note_host_config_sig() -> None:
    global _HOST_CONFIG_SIG
    _HOST_CONFIG_SIG = config.host_config_signature()


def _providers_view() -> dict:
    """Comparable snapshot of every configured endpoint, for change
    detection in `reload_host_config`."""
    return {
        name: (
            str(conf.get("base_url", "")),
            str(conf.get("api_key_env", "")),
            bool(conf.get("thinking", False)),
        )
        for name, conf in config.PROVIDERS.items()
    }


def reload_host_config() -> list[str]:
    """Pull the mounted host `config.toml` into the live settings. Host wins:
    every managed knob the file defines beats its overlay copy (which is
    pruned) so a hand edit outside the container actually takes effect.
    Returns the knobs and endpoints whose effective value changed."""
    path = config.host_config_path()
    if not path.is_file():
        return []
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []

    before = {field["key"]: _dotted(config.SETTINGS, field["key"]) for field in RUNTIME_FIELDS}
    before_providers = _providers_view()
    present = set()
    for field in RUNTIME_FIELDS:
        section, _, name = field["key"].partition(".")
        group = data.get(section)
        if isinstance(group, dict) and name in group:
            present.add(field["key"])

    current = config.read_settings_overlay()
    pruned: dict = {}
    for section, items in current.items():
        if not isinstance(items, dict):
            pruned[section] = items
            continue
        keep = {
            name: value
            for name, value in items.items()
            if f"{section}.{name}" not in present
        }
        if keep:
            pruned[section] = keep
    if pruned != current:
        config.write_settings_overlay(pruned)
    config.refresh_runtime()

    after = {field["key"]: _dotted(config.SETTINGS, field["key"]) for field in RUNTIME_FIELDS}
    changed = sorted(key for key in before if before[key] != after[key])
    after_providers = _providers_view()
    for name in sorted(set(before_providers) | set(after_providers)):
        if before_providers.get(name) != after_providers.get(name):
            changed.append(f"llm.providers.{name}")
    changed.sort()
    _note_host_config_sig()
    if changed or pruned != current:
        log.info(
            "reloaded host config.toml via settings: %s",
            ", ".join(changed) if changed else "(overrides dropped)",
        )
    return changed


def _split_inline_comment(text: str) -> str:
    """Return the `# comment` tail of a TOML line, ignoring `#` inside
    strings."""
    quote = ""
    escaped = False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#":
            return text[index:]
    return ""


def _toml_depth(text: str) -> int:
    """Net open brackets ({, [) of a TOML line, outside strings and the
    inline comment. Non-zero while a multi-line value is still open."""
    comment = _split_inline_comment(text)
    body = text[: len(text) - len(comment)] if comment else text
    quote = ""
    escaped = False
    depth = 0
    for char in body:
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
    return max(depth, 0)


def _toml_section(line: str) -> str | None:
    """The name of a `[section]` header line, or None for anything else.
    Quoted parts are unquoted, so `[llm.providers."9router"]` and
    `[llm.providers.9router]` resolve to the same table."""
    stripped = line.strip()
    comment = _split_inline_comment(stripped)
    if comment:
        stripped = stripped[: len(stripped) - len(comment)].rstrip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return None
    name = stripped[1:-1].strip()
    if not name:
        return None
    return ".".join(part.strip().strip('"').strip("'") for part in name.split("."))


_KEY_RE = re.compile(r'^("[^"]+"|[A-Za-z0-9_.-]+)\s*=')


def _update_toml_file(
    path,
    updates: dict[str, object],
    remove: set[str] | None = None,
) -> None:
    """Rewrite `key = value` lines in place for dotted-key updates,
    preserving comments, blank lines, order, and unrelated keys; missing keys
    are added under their section header, missing sections at the end.
    ``remove`` drops whole sections (header plus its body). Values are written
    single-line, so a multi-line value being replaced is consumed whole.
    In-place (open+truncate) is deliberate: the host `config.toml` is a
    bind-mounted file, and renames onto a mount point fail with EBUSY, so
    temp-file-and-rename writers cannot work here."""
    try:
        text = path.read_text()
    except OSError as exc:
        raise ValueError(f"could not read host config.toml: {exc}")

    removed = set(remove or ())
    pending = dict(updates)
    lines = text.splitlines()
    sections: dict[str, list[str]] = {}
    for key in pending:
        section, _, _ = key.rpartition(".")
        sections.setdefault(section, []).append(key)

    # pass 1: which pending keys already have a line in the file?
    found: set[str] = set()
    section = ""
    for line in lines:
        header = _toml_section(line)
        if header is not None:
            section = header
            continue
        match = _KEY_RE.match(line.strip())
        if match:
            found.add(f"{section}.{match.group(1)}" if section else match.group(1))

    # pass 2: rewrite values, insert missing keys under their headers, drop
    # removed sections whole
    inserted: set[str] = set()
    out: list[str] = []
    section = ""
    skip_depth = 0
    dropping = False
    for line in lines:
        if skip_depth:
            # still inside the multi-line value being replaced
            skip_depth += _toml_depth(line)
            if skip_depth <= 0:
                skip_depth = 0
            continue
        header = _toml_section(line)
        if header is not None:
            section = header
            dropping = header in removed
            if dropping:
                continue
            out.append(line)
            for key in sections.get(section, ()):
                if key not in found and key not in inserted:
                    name = key.rpartition(".")[2]
                    out.append(f"{name} = {config._toml_value(pending[key])}")
                    inserted.add(key)
            continue
        if dropping:
            continue
        stripped = line.strip()
        match = _KEY_RE.match(stripped)
        if match:
            key = f"{section}.{match.group(1)}" if section else match.group(1)
            if key in pending:
                name = match.group(1)
                indent = line[: len(line) - len(line.lstrip())]
                depth = _toml_depth(stripped)
                comment = _split_inline_comment(stripped)
                value = config._toml_value(pending.pop(key))
                out.append(f"{indent}{name} = {value}{' ' + comment if comment else ''}")
                if depth > 0:
                    skip_depth = depth
                continue
        out.append(line)

    # sections the file does not have yet go at the end
    for section in sorted(sections):
        rest = [key for key in sections[section] if key not in found and key not in inserted]
        if not rest:
            continue
        if out and out[-1].strip():
            out.append("")
        if section:
            out.append(f"[{section}]")
        for key in rest:
            name = key.rpartition(".")[2]
            out.append(f"{name} = {config._toml_value(pending[key])}")

    try:
        with path.open("w") as fh:
            fh.write("\n".join(out) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        raise ValueError(f"could not write host config.toml: {exc}")


def push_host_config(values: dict[str, str]) -> list[str]:
    """Write staged runtime knobs through to the mounted host `config.toml`
    (comments and unrelated keys are preserved), then drop their overlay
    copies so the file is the single source of truth again."""
    path = config.host_config_path()
    if not path.is_file():
        raise ValueError("host config.toml is not mounted in this container")
    if not os.access(path, os.W_OK):
        raise ValueError("host config.toml is not writable (check the file permissions)")
    pending = _coerce_values(values)
    if not pending:
        return []
    _update_toml_file(path, pending)

    current = config.read_settings_overlay()
    pruned: dict = {}
    for section, items in current.items():
        if not isinstance(items, dict):
            pruned[section] = items
            continue
        keep = {
            name: value
            for name, value in items.items()
            if f"{section}.{name}" not in pending
        }
        if keep:
            pruned[section] = keep
    if pruned != current:
        config.write_settings_overlay(pruned)
    config.refresh_runtime()
    _note_host_config_sig()
    log.info("pushed runtime config to host config.toml via settings: %s", sorted(pending))
    return sorted(pending)


# Model endpoints (`llm.providers`). Unlike the scalar runtime knobs these are
# structured tables, so they live directly in the host `config.toml` with no
# overlay: the file is the single source of truth, and editing needs it
# mounted and writable. Saving refreshes the runtime so new endpoints, base
# URLs and thinking flags apply without a restart.
_ENDPOINT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def endpoints_status() -> dict:
    """Every configured model endpoint for the Settings UI: base URL, key
    var, thinking flag, whether it is the default, and whether its key is
    set (values stay masked)."""
    host = host_config_state()
    entries = []
    for name, conf in config.PROVIDERS.items():
        key_var = str(conf.get("api_key_env", ""))
        entries.append(
            {
                "name": name,
                "base_url": str(conf.get("base_url", "")),
                "api_key_env": key_var,
                "thinking": bool(conf.get("thinking", False)),
                "default": name == config.PROVIDER,
                "key_configured": bool(os.getenv(key_var, "").strip()) if key_var else False,
            }
        )
    return {"endpoints": entries, "mounted": host["mounted"], "writable": host["writable"]}


def _endpoint_values(values: dict) -> dict:
    name = str(values.get("name", "")).strip()
    if not _ENDPOINT_RE.match(name):
        raise ValueError(
            "endpoint name must start with a letter or digit and use only letters, digits, '-' or '_'"
        )
    base_url = str(values.get("base_url", "")).strip()
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base URL must start with http:// or https://")
    api_key_env = str(values.get("api_key_env", "")).strip()
    if not _VAR_RE.match(api_key_env):
        raise ValueError(
            "key variable name must be upper-case letters, digits and '_' (e.g. NEWROUTER_API_KEY)"
        )
    return {
        "name": name,
        "base_url": base_url,
        "api_key_env": api_key_env,
        "thinking": bool(values.get("thinking")),
    }


def save_endpoint(values: dict) -> dict:
    """Create or update a model endpoint in the mounted host `config.toml`."""
    endpoint = _endpoint_values(values)
    path = config.host_config_path()
    if not path.is_file():
        raise ValueError("host config.toml is not mounted in this container")
    if not os.access(path, os.W_OK):
        raise ValueError("host config.toml is not writable (check the file permissions)")
    name = endpoint["name"]
    _update_toml_file(
        path,
        {
            f"llm.providers.{name}.base_url": endpoint["base_url"],
            f"llm.providers.{name}.api_key_env": endpoint["api_key_env"],
            f"llm.providers.{name}.thinking": endpoint["thinking"],
        },
    )
    config.refresh_runtime()
    _note_host_config_sig()
    log.info("saved endpoint %s via settings", name)
    return endpoints_status()


def delete_endpoint(name: str) -> dict:
    """Remove a model endpoint from the mounted host `config.toml`."""
    name = str(name or "").strip()
    if name not in config.PROVIDERS:
        raise ValueError(f"unknown endpoint {name!r}")
    if name == config.PROVIDER:
        raise ValueError("this endpoint is the default; switch the default provider first")
    path = config.host_config_path()
    if not path.is_file():
        raise ValueError("host config.toml is not mounted in this container")
    if not os.access(path, os.W_OK):
        raise ValueError("host config.toml is not writable (check the file permissions)")
    _update_toml_file(path, {}, remove={f"llm.providers.{name}"})
    config.refresh_runtime()
    _note_host_config_sig()
    log.info("deleted endpoint %s via settings", name)
    return endpoints_status()

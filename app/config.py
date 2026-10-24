import hashlib
import json
import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "server": {"host": "0.0.0.0", "port": 4517},
    "llm": {
        "provider": "deepseek",
        "model": "deepseek-flash",
        "effort": "high",
        "efforts": ["low", "high", "max"],
        "request_timeout": 180,
        "temperature": 0.3,
        "limits": {},
        "providers": {
            "deepseek": {
                "base_url": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY",
                "thinking": True,
            },
        },
    },
    "storage": {"data_dir": "data"},
    "skills": {"dirs": ["skills"]},
    "agent": {
        "instructions": ["AGENTS.md", "prompts/operator_policy.md",
                          "prompts/findings_report.md", "prompts/supervisor_policy.md"],
        "seed_history": "prompts/seed_history.json",
    },
    "uploads": {
        "max_bytes": 10 * 1024 * 1024,
        "max_files_per_field": 5,
        "file_char_cap": 50_000,
        "total_char_cap": 120_000,
    },
    "tools": {
        "max_steps": 300,
        # tail of the step budget reserved for reporting: once this many steps
        # remain, only the reporting tools are offered so a worker can always
        # write its evidence and close out instead of dying mid-exploit.
        "report_reserve_steps": 20,
        "bash_timeout": 120,
        "bash_output_bytes": 10 * 1024 * 1024,
        "output_chars": 20_000,
        "read_max_lines": 2000,
        "http_timeout": 30,
    },
    "supervisor": {
        "error_backoff_seconds": 30,
        "compact_at_tokens": 0,
        "compact_keep_messages": 8,
    },
    "hypotheses": {
        "max_generators": 5,
        # hunter workers: how many may run at once, and how many hypotheses one
        # worker is handed per dispatch.
        "max_workers": 5,
        "max_hypotheses_per_worker": 3,
    },
    "vm": {
        # Kernel-pentest VM. The hypervisor lives on the host (vm/vmctl.py);
        # this is only the socket the container is allowed to talk to.
        "sock": "/app/vm/run/vm.sock",
        "call_timeout": 30,
        "exec_timeout": 120,
    },
}


def load(path=None) -> dict:
    settings = {name: dict(section) for name, section in DEFAULTS.items()}

    file = Path(path or os.getenv("L4RP_CONFIG") or ROOT / "config.toml")
    if file.is_file():
        with file.open("rb") as fh:
            given = tomllib.load(fh)
        for section, values in given.items():
            settings.setdefault(section, {}).update(values)

    return settings


SETTINGS = load()

SERVER_HOST = SETTINGS["server"]["host"]
SERVER_PORT = SETTINGS["server"]["port"]

PROVIDERS = SETTINGS["llm"]["providers"]
PROVIDER = SETTINGS["llm"]["provider"]
PROVIDER_CONF = PROVIDERS.get(PROVIDER, {})
LIMITS = SETTINGS["llm"].get("limits", {}) or {}

data_dir = os.getenv("L4RP_DATA_DIR") or SETTINGS["storage"]["data_dir"]
DATA_DIR = Path(data_dir)
if not DATA_DIR.is_absolute():
    DATA_DIR = ROOT / DATA_DIR

# UI-managed provider keys (Settings page). The overlay lives in the data
# volume so it survives container recreates; the process env (host `.env` via
# compose `env_file`, or a local `.env`) is the base layer and the overlay
# wins. Applied here so API_KEY/CONFIGURED below see the merged values.
ENV_OVERLAY_NAME = ".env.overlay"
_ENV_BASELINE: dict[str, str] = {}
_ENV_BASELINED = False


def _provider_key_vars() -> list[str]:
    return [
        str(conf.get("api_key_env", ""))
        for conf in PROVIDERS.values()
        if conf.get("api_key_env")
    ]


def read_env_overlay() -> dict[str, str]:
    values: dict[str, str] = {}
    path = DATA_DIR / ENV_OVERLAY_NAME
    if not path.is_file():
        return values
    allowed = set(_provider_key_vars())
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name in allowed:
            values[name] = value.strip()
    return values


def apply_env_overlay() -> dict[str, str]:
    """Merge the overlay into the process env (overlay wins). The pre-overlay
    process env is snapshotted once so clearing a UI-managed key restores it."""
    global _ENV_BASELINED
    if not _ENV_BASELINED:
        for name in _provider_key_vars():
            if name in os.environ and name not in _ENV_BASELINE:
                _ENV_BASELINE[name] = os.environ[name]
        _ENV_BASELINED = True
    values = read_env_overlay()
    for name, value in values.items():
        os.environ[name] = value
    return values


def refresh_env() -> None:
    """Re-resolve every provider key var (overlay, else original process env)
    and recompute the key-derived constants. Called after the Settings UI
    saves, so the new keys apply without a restart."""
    overlay = read_env_overlay()
    for name in _provider_key_vars():
        if name in overlay:
            os.environ[name] = overlay[name]
        elif name in _ENV_BASELINE:
            os.environ[name] = _ENV_BASELINE[name]
        else:
            os.environ.pop(name, None)
    reload_keys()


def reload_keys() -> None:
    global API_KEY, CONFIGURED
    API_KEY = os.getenv(PROVIDER_CONF.get("api_key_env", "DEEPSEEK_API_KEY"), "").strip()
    CONFIGURED = any(
        os.getenv(conf.get("api_key_env", ""), "").strip() for conf in PROVIDERS.values()
    )


# UI-managed runtime config (Settings page, "Model & runtime" section). Same
# persistence story as the key overlay: a TOML file in the data volume that is
# deep-merged over config.toml. Only whitelisted scalar/list knobs are ever
# written here — server binding, storage paths, and provider wiring stay
# config.toml-only and need a recreate. When the host config.toml is mounted
# and writable, the settings module also writes values through to it and drops
# the overlay copies (the file becomes the single source of truth), mirroring
# the .env flow.
SETTINGS_OVERLAY_NAME = "settings.overlay.toml"


def _deep_merge(base: dict, over: dict) -> dict:
    merged = {name: dict(section) for name, section in base.items()}
    for section, values in over.items():
        if not isinstance(values, dict):
            continue
        target = merged.setdefault(section, {})
        for key, value in values.items():
            target[key] = value
    return merged


def read_settings_overlay() -> dict:
    path = DATA_DIR / SETTINGS_OVERLAY_NAME
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[ " + ", ".join(_toml_value(item) for item in value) + " ]"
    raise ValueError(f"unsupported value {value!r}")


def write_settings_overlay(values: dict) -> None:
    """Persist nested {section: {key: scalar|list}} overrides as TOML."""
    path = DATA_DIR / SETTINGS_OVERLAY_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Managed by the Settings UI. Whitelisted runtime knobs; merged over config.toml.",
        "",
    ]
    for section in sorted(values):
        items = values[section]
        if not isinstance(items, dict) or not items:
            continue
        lines.append(f"[{section}]")
        for key in sorted(items):
            lines.append(f"{key} = {_toml_value(items[key])}")
        lines.append("")
    path.write_text("\n".join(lines))
    os.chmod(path, 0o600)


def refresh_runtime() -> None:
    """Re-read config.toml + the settings overlay and recompute every
    runtime-safe constant, so Settings saves apply without a restart.
    Server binding, storage paths, and provider wiring are intentionally
    left alone (they need a recreate)."""
    global SETTINGS, PROVIDERS, PROVIDER, PROVIDER_CONF, LIMITS
    global BASE_URL, THINKING, API_KEY, CONFIGURED
    global DEFAULT_MODEL, DEFAULT_EFFORT, EFFORTS, REQUEST_TIMEOUT, TEMPERATURE
    global SUPERVISOR_BACKOFF, COMPACT_AT_TOKENS, COMPACT_KEEP_MESSAGES
    global MAX_GENERATORS, MAX_WORKERS, MAX_HYPOTHESES_PER_WORKER
    global VM_SOCK, VM_CALL_TIMEOUT, VM_EXEC_TIMEOUT
    global MAX_TOOL_STEPS, REPORT_RESERVE_STEPS, BASH_TIMEOUT, BASH_OUTPUT_BYTES
    global TOOL_OUTPUT_CHARS, READ_MAX_LINES, HTTP_TIMEOUT
    global MAX_UPLOAD_BYTES, MAX_FILES_PER_FIELD, FILE_CHAR_CAP, TOTAL_CHAR_CAP

    SETTINGS = _deep_merge(load(), read_settings_overlay())
    PROVIDERS = SETTINGS["llm"]["providers"]
    PROVIDER = SETTINGS["llm"]["provider"]
    PROVIDER_CONF = PROVIDERS.get(PROVIDER, {})
    LIMITS = SETTINGS["llm"].get("limits", {}) or {}
    BASE_URL = PROVIDER_CONF.get("base_url", "https://api.deepseek.com")
    THINKING = bool(PROVIDER_CONF.get("thinking", False))
    DEFAULT_MODEL = SETTINGS["llm"]["model"]
    DEFAULT_EFFORT = SETTINGS["llm"]["effort"]
    EFFORTS = list(SETTINGS["llm"]["efforts"])
    REQUEST_TIMEOUT = SETTINGS["llm"]["request_timeout"]
    TEMPERATURE = SETTINGS["llm"]["temperature"]
    SUPERVISOR_BACKOFF = SETTINGS["supervisor"]["error_backoff_seconds"]
    COMPACT_AT_TOKENS = SETTINGS["supervisor"]["compact_at_tokens"]
    COMPACT_KEEP_MESSAGES = SETTINGS["supervisor"]["compact_keep_messages"]
    MAX_GENERATORS = SETTINGS["hypotheses"]["max_generators"]
    MAX_WORKERS = SETTINGS["hypotheses"]["max_workers"]
    MAX_HYPOTHESES_PER_WORKER = SETTINGS["hypotheses"]["max_hypotheses_per_worker"]
    VM_SOCK = SETTINGS["vm"]["sock"]
    VM_CALL_TIMEOUT = SETTINGS["vm"]["call_timeout"]
    VM_EXEC_TIMEOUT = SETTINGS["vm"]["exec_timeout"]
    MAX_TOOL_STEPS = SETTINGS["tools"]["max_steps"]
    REPORT_RESERVE_STEPS = SETTINGS["tools"]["report_reserve_steps"]
    BASH_TIMEOUT = SETTINGS["tools"]["bash_timeout"]
    BASH_OUTPUT_BYTES = SETTINGS["tools"]["bash_output_bytes"]
    TOOL_OUTPUT_CHARS = SETTINGS["tools"]["output_chars"]
    READ_MAX_LINES = SETTINGS["tools"]["read_max_lines"]
    HTTP_TIMEOUT = SETTINGS["tools"]["http_timeout"]
    MAX_UPLOAD_BYTES = SETTINGS["uploads"]["max_bytes"]
    MAX_FILES_PER_FIELD = SETTINGS["uploads"]["max_files_per_field"]
    FILE_CHAR_CAP = SETTINGS["uploads"]["file_char_cap"]
    TOTAL_CHAR_CAP = SETTINGS["uploads"]["total_char_cap"]
    reload_keys()


SETTINGS = _deep_merge(SETTINGS, read_settings_overlay())

# The host `.env` bind-mounted into the container (compose.yaml). Unlike the
# injected process env, this file stays visible, so the Settings UI can detect
# outside edits and — when writable — rewrite keys through it.
HOST_ENV_NAME = ".env"


def host_env_path() -> Path:
    return ROOT / HOST_ENV_NAME


def host_env_signature() -> str | None:
    """Content hash of the mounted host `.env`, or None when it is not a
    readable file (not mounted, or shadowed by a directory)."""
    path = host_env_path()
    try:
        if not path.is_file():
            return None
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return None


# The host `config.toml` bind-mounted into the container (compose.yaml). Like
# the mounted `.env` it stays visible, so the Settings UI can detect outside
# edits and — when writable — write runtime knobs through it.
HOST_CONFIG_NAME = "config.toml"


def host_config_path() -> Path:
    return ROOT / HOST_CONFIG_NAME


def host_config_signature() -> str | None:
    """Content hash of the mounted host `config.toml`, or None when it is not
    a readable file (not mounted, or shadowed by a directory)."""
    path = host_config_path()
    try:
        if not path.is_file():
            return None
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return None


apply_env_overlay()

API_KEY = os.getenv(PROVIDER_CONF.get("api_key_env", "DEEPSEEK_API_KEY"), "").strip()
BASE_URL = PROVIDER_CONF.get("base_url", "https://api.deepseek.com")
THINKING = bool(PROVIDER_CONF.get("thinking", False))
CONFIGURED = any(
    os.getenv(conf.get("api_key_env", ""), "").strip() for conf in PROVIDERS.values()
)
DEFAULT_MODEL = SETTINGS["llm"]["model"]
DEFAULT_EFFORT = SETTINGS["llm"]["effort"]
EFFORTS = list(SETTINGS["llm"]["efforts"])
REQUEST_TIMEOUT = SETTINGS["llm"]["request_timeout"]
TEMPERATURE = SETTINGS["llm"]["temperature"]

DB_PATH = DATA_DIR / "l4rpness.db"
UPLOAD_DIR = DATA_DIR / "uploads"
WORK_DIR = DATA_DIR / "work"
WEB_DIST = ROOT / "web" / "dist"

SKILL_DIRS = [
    Path(path) if Path(path).is_absolute() else ROOT / path
    for path in SETTINGS["skills"]["dirs"]
]

INSTRUCTION_FILES = [
    Path(path) if Path(path).is_absolute() else ROOT / path
    for path in SETTINGS["agent"]["instructions"]
]

seed_history = SETTINGS["agent"].get("seed_history") or ""
SEED_HISTORY = (Path(seed_history) if Path(seed_history).is_absolute() else ROOT / seed_history) if seed_history else None

MAX_UPLOAD_BYTES = SETTINGS["uploads"]["max_bytes"]
MAX_FILES_PER_FIELD = SETTINGS["uploads"]["max_files_per_field"]
FILE_CHAR_CAP = SETTINGS["uploads"]["file_char_cap"]
TOTAL_CHAR_CAP = SETTINGS["uploads"]["total_char_cap"]

SUPERVISOR_BACKOFF = SETTINGS["supervisor"]["error_backoff_seconds"]
COMPACT_AT_TOKENS = SETTINGS["supervisor"]["compact_at_tokens"]
COMPACT_KEEP_MESSAGES = SETTINGS["supervisor"]["compact_keep_messages"]
MAX_GENERATORS = SETTINGS["hypotheses"]["max_generators"]
MAX_WORKERS = SETTINGS["hypotheses"]["max_workers"]
MAX_HYPOTHESES_PER_WORKER = SETTINGS["hypotheses"]["max_hypotheses_per_worker"]

VM_SOCK = SETTINGS["vm"]["sock"]
VM_CALL_TIMEOUT = SETTINGS["vm"]["call_timeout"]
VM_EXEC_TIMEOUT = SETTINGS["vm"]["exec_timeout"]

MAX_TOOL_STEPS = SETTINGS["tools"]["max_steps"]
REPORT_RESERVE_STEPS = SETTINGS["tools"]["report_reserve_steps"]
BASH_TIMEOUT = SETTINGS["tools"]["bash_timeout"]
BASH_OUTPUT_BYTES = SETTINGS["tools"]["bash_output_bytes"]
TOOL_OUTPUT_CHARS = SETTINGS["tools"]["output_chars"]
READ_MAX_LINES = SETTINGS["tools"]["read_max_lines"]
HTTP_TIMEOUT = SETTINGS["tools"]["http_timeout"]

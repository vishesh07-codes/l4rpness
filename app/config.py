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
        "instructions": ["AGENTS.md", "prompts/operator_policy.md"],
        "seed_history": "prompts/seed_history.json",
    },
    "uploads": {
        "max_bytes": 10 * 1024 * 1024,
        "max_files_per_field": 5,
        "file_char_cap": 50_000,
        "total_char_cap": 120_000,
    },
    "tools": {
        "max_steps": -1,
        "bash_timeout": 120,
        "bash_output_bytes": 10 * 1024 * 1024,
        "output_chars": 20_000,
        "read_max_lines": 2000,
        "http_timeout": 30,
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

PROVIDER = SETTINGS["llm"]["provider"]
PROVIDER_CONF = SETTINGS["llm"]["providers"].get(PROVIDER, {})

API_KEY = os.getenv(PROVIDER_CONF.get("api_key_env", "DEEPSEEK_API_KEY"), "").strip()
BASE_URL = PROVIDER_CONF.get("base_url", "https://api.deepseek.com")
THINKING = bool(PROVIDER_CONF.get("thinking", False))
DEFAULT_MODEL = SETTINGS["llm"]["model"]
DEFAULT_EFFORT = SETTINGS["llm"]["effort"]
EFFORTS = list(SETTINGS["llm"]["efforts"])
REQUEST_TIMEOUT = SETTINGS["llm"]["request_timeout"]
TEMPERATURE = SETTINGS["llm"]["temperature"]

data_dir = os.getenv("L4RP_DATA_DIR") or SETTINGS["storage"]["data_dir"]
DATA_DIR = Path(data_dir)
if not DATA_DIR.is_absolute():
    DATA_DIR = ROOT / DATA_DIR

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

MAX_TOOL_STEPS = SETTINGS["tools"]["max_steps"]
BASH_TIMEOUT = SETTINGS["tools"]["bash_timeout"]
BASH_OUTPUT_BYTES = SETTINGS["tools"]["bash_output_bytes"]
TOOL_OUTPUT_CHARS = SETTINGS["tools"]["output_chars"]
READ_MAX_LINES = SETTINGS["tools"]["read_max_lines"]
HTTP_TIMEOUT = SETTINGS["tools"]["http_timeout"]

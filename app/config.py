import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "server": {"host": "0.0.0.0", "port": 4517},
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "effort": "high",
        "efforts": ["low", "high", "max"],
        "request_timeout": 180,
        "temperature": 0.3,
    },
    "storage": {"data_dir": "data"},
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
    "pricing": {
        # USD per 1M tokens, used only to show billed spend. Leave at 0 to
        # report token usage with no dollar amount.
        "input": 0.0,
        "cached_input": 0.0,
        "output": 0.0,
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

API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
BASE_URL = SETTINGS["deepseek"]["base_url"]
DEFAULT_MODEL = SETTINGS["deepseek"]["model"]
DEFAULT_EFFORT = SETTINGS["deepseek"]["effort"]
EFFORTS = list(SETTINGS["deepseek"]["efforts"])
REQUEST_TIMEOUT = SETTINGS["deepseek"]["request_timeout"]
TEMPERATURE = SETTINGS["deepseek"]["temperature"]

data_dir = os.getenv("L4RP_DATA_DIR") or SETTINGS["storage"]["data_dir"]
DATA_DIR = Path(data_dir)
if not DATA_DIR.is_absolute():
    DATA_DIR = ROOT / DATA_DIR

DB_PATH = DATA_DIR / "l4rpness.db"
UPLOAD_DIR = DATA_DIR / "uploads"
WORK_DIR = DATA_DIR / "work"
WEB_DIST = ROOT / "web" / "dist"

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

PRICE_INPUT = float(SETTINGS["pricing"]["input"])
PRICE_CACHED_INPUT = float(SETTINGS["pricing"]["cached_input"])
PRICE_OUTPUT = float(SETTINGS["pricing"]["output"])


def cost_usd(input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    """Billed spend from configured per-1M-token prices; 0 until prices are set."""
    uncached = max(input_tokens - cached_tokens, 0)
    return round(
        uncached / 1_000_000 * PRICE_INPUT
        + cached_tokens / 1_000_000 * PRICE_CACHED_INPUT
        + output_tokens / 1_000_000 * PRICE_OUTPUT,
        6,
    )

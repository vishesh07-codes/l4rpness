import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.getenv("L4RP_DATA_DIR") or ROOT / "data")
DB_PATH = DATA_DIR / "l4rpness.db"
UPLOAD_DIR = DATA_DIR / "uploads"
WEB_DIST = ROOT / "web" / "dist"

API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
DEFAULT_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()

MODELS = ["deepseek-chat", "deepseek-flash", "deepseek-reasoner"]

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_FIELD = 5
FILE_CHAR_CAP = 50_000
TOTAL_CHAR_CAP = 120_000

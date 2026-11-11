import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
SESSION_DIR = BASE_DIR / "uzum_session"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
DB_PATH = BASE_DIR / "uzum_sniper.db"

SESSION_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
HEADLESS = os.getenv("HEADLESS", "false").lower() in ("true", "1", "yes")
MIN_CHECK_INTERVAL = int(os.getenv("MIN_INTERVAL", "45"))
MAX_CHECK_INTERVAL = int(os.getenv("MAX_INTERVAL", "90"))

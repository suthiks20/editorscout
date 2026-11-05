import os
from pathlib import Path
from secrets import token_urlsafe

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY") or token_urlsafe(32)
    SERPER_API_KEY = os.getenv("SERPER_API_KEY", "").strip()
    SERPER_URL = "https://google.serper.dev/search"
    SERPER_TIMEOUT_SECONDS = 15
    INCUBRIX_ONE_LINER = os.getenv(
        "INCUBRIX_ONE_LINER",
        "IncuBrix helps creators turn great ideas into compelling video content.",
    ).strip()
    INCUBRIX_OFFER = os.getenv(
        "INCUBRIX_OFFER",
        "I'd be glad to share how IncuBrix could support your next project.",
    ).strip()
    DATABASE_PATH = os.getenv(
        "DATABASE_PATH", str(BASE_DIR / "instance" / "editorscout.sqlite3")
    )

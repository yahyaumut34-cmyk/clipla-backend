import os
from dotenv import load_dotenv
from pathlib import Path
from typing import Set

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


class Config:
    # Anthropic
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # Admin
    ADMIN_PASSWORD: str = os.getenv("CLIPLA_ADMIN_PASSWORD", "clipla2026")

    # Whisper
    WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "small")

    # Storage
    UPLOAD_DIR:  Path = BASE_DIR / "uploads"
    OUTPUT_DIR:  Path = BASE_DIR / "outputs"
    STATIC_DIR:  Path = BASE_DIR / "static"

    # Limits
    MAX_FILE_SIZE: int = 500 * 1024 * 1024  # 500 MB

    # Demo tokens (virgülle ayrılmış, boşsa herkese açık)
    DEMO_TOKENS: str = os.getenv("DEMO_TOKENS", "")

    def demo_token_set(self) -> Set[str]:
        if not self.DEMO_TOKENS:
            return set()
        return {t.strip() for t in self.DEMO_TOKENS.split(",") if t.strip()}


config = Config()

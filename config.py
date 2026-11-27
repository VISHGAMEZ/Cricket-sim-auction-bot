from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Config:
    token: str
    bot_admin_id: int
    database_url: str
    default_prefix: str = "c!"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls):
        token = os.getenv("DISCORD_TOKEN", "").strip()
        admin = os.getenv("BOT_ADMIN_ID", "").strip()
        if not token:
            raise RuntimeError("DISCORD_TOKEN is required (set it in .env)")
        if not admin.isdigit():
            raise RuntimeError("BOT_ADMIN_ID must be a Discord user ID")
        return cls(token, int(admin), os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./auction.db"),
                   os.getenv("DEFAULT_PREFIX", "c!"), os.getenv("LOG_LEVEL", "INFO"))

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runner configuration, read from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Shared secret the orchestrator sends in the X-Runner-Secret header.
    runner_secret: str
    # Local Telegram Bot API server.
    bot_api_url: str = "http://telegram-bot-api:8081"
    # Orchestrator base URL, used by ctx.kv.
    orchestrator_url: str = "http://bot-orchestrator:8000"
    # Root folder for bot files; each bot gets <files_root>/<bot id>.
    files_root: str = "/files"
    # Data directory of the local Bot API server (same path in every container).
    bot_api_data_dir: str = "/var/lib/telegram-bot-api"
    # Hard limit for a single handler run, in seconds.
    handler_timeout: float = 60.0
    # Address-space limit for a handler process, in megabytes (0 = no limit).
    handler_memory_mb: int = 1024
    # Maximum number of handler processes running at the same time.
    max_concurrent_runs: int = 8


@lru_cache
def get_settings() -> Settings:
    return Settings()

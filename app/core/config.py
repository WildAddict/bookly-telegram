from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str = "postgresql+asyncpg://bookly:bookly@localhost:5432/bookly"
    admin_telegram_ids: str = ""
    business_name: str = "Bookly"
    default_timezone: str = "Europe/Minsk"
    booking_slot_minutes: int = 15
    reminder_poll_seconds: int = 60

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def admin_ids(self) -> set[int]:
        return {int(item.strip()) for item in self.admin_telegram_ids.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    llm_provider: Literal["gemini", "openrouter", "ollama"] = "gemini"
    gemini_api_key: str = ""
    gemma_model: str = "gemma-4-26b-a4b-it"
    gemma_fallback_model: str = "gemma-4-31b-it"
    llm_models: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemma-4-26b-a4b-it"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemma-4-26b-a4b-it:free"

    embed_provider: Literal["none", "bge"] = "bge"
    embed_model: str = "BAAI/bge-large-en-v1.5"

    database_url: str = f"sqlite:///{PROJECT_ROOT / 'data' / 'wellwell.db'}"
    media_dir: Path = PROJECT_ROOT / "data" / "media"
    synthetic_dir: Path = PROJECT_ROOT / "data" / "raw" / "synthetic"

    playbook_enabled: bool = True
    agent_max_steps: int = 6
    agent_timeout_s: int = 150
    llm_call_timeout_s: int = 90
    sql_row_limit: int = 200

    log_level: str = "INFO"
    api_base_url: str = "http://localhost:8765"
    demo_user: str = "S. Patil"
    today: str = "2026-10-17"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.database_url.startswith("sqlite:///./"):
        relative = settings.database_url.removeprefix("sqlite:///./")
        settings.database_url = f"sqlite:///{PROJECT_ROOT / relative}"
    return settings

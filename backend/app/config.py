from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND = Path(__file__).resolve().parents[1]
_ENV_FILES = (str(_BACKEND.parent / ".env"), str(_BACKEND / ".env"))  # project root, then backend/

DEFAULT_SECRET = "dev-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILES, extra="ignore")

    env: str = "dev"  # anything other than "dev" enforces production checks
    database_url: str = "sqlite:///./tasmac.db"
    secret_key: str = DEFAULT_SECRET
    access_token_minutes: int = 480
    cors_origins: str = "http://localhost:5173"

    # AI provider: one Gemini API key for analysis and embeddings.
    # Without a key the system uses local heuristic providers.
    google_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"
    embedding_model: str = "models/gemini-embedding-001"
    embedding_dim: int = 768  # must match the grievance_embeddings column
    prompt_version: str = "v1"

    # Similarity thresholds. None means "use the provider default".
    duplicate_threshold: float | None = None
    related_threshold: float | None = None
    rerank_threshold: float | None = None
    duplicate_window_days: int = 14
    similarity_window_days: int = 180
    recurring_window_days: int = 90
    recurring_min_count: int = 3
    review_confidence_threshold: float = 0.6

    # The shop locations, products and stock are demo records until real inventory is connected.
    store_data_is_sample: bool = True

    # Operations
    public_submissions_per_hour: int = 10
    login_attempts_per_15min: int = 10
    run_escalation_loop: bool = True
    escalation_interval_minutes: int = 10
    reopen_window_days: int = 14

    def check_production(self) -> None:
        if self.env != "dev" and self.secret_key == DEFAULT_SECRET:
            raise RuntimeError("SECRET_KEY must be set when ENV is not 'dev'")


settings = Settings()

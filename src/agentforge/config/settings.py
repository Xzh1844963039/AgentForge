"""Centralized, environment-driven configuration.

All runtime configuration is read from environment variables (optionally via a
local ``.env`` file) with the ``AGENTFORGE_`` prefix. API keys are never
hard-coded. The standard ``DEEPSEEK_API_KEY`` / ``HKGAI_API_KEY`` names are also
accepted so the project works with conventional course setups.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["mock", "deepseek", "hkgai"]


class Settings(BaseSettings):
    """AgentForge runtime settings.

    Values can be supplied as constructor arguments (useful for tests) or from
    environment variables / ``.env``.
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENTFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # --- LLM provider selection -------------------------------------------------
    llm_provider: ProviderName = "mock"

    deepseek_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AGENTFORGE_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"
        ),
    )
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    hkgai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENTFORGE_HKGAI_API_KEY", "HKGAI_API_KEY"),
    )
    hkgai_base_url: str | None = None
    hkgai_model: str | None = None

    # --- LLM behaviour ----------------------------------------------------------
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=64)
    request_timeout: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    mock_latency_ms: int = Field(default=0, ge=0, le=30_000)

    # --- Orchestration safety limits -------------------------------------------
    max_plan_subtasks: int = Field(default=8, ge=1, le=30)
    max_delegation_depth: int = Field(default=3, ge=0, le=10)
    max_delegation_rounds: int = Field(default=2, ge=0, le=10)
    max_execution_steps: int = Field(default=30, ge=1, le=200)
    max_tasks_per_workflow: int = Field(default=60, ge=1, le=500)
    enable_final_review: bool = True

    # --- Persistence / observability -------------------------------------------
    data_dir: Path = Path("data")
    sqlite_file: Path | None = None
    events_file: Path | None = None
    log_level: str = "INFO"
    log_file: Path | None = None
    task_history_limit: int = Field(default=200, ge=1)

    @property
    def resolved_sqlite_file(self) -> Path:
        return self.sqlite_file or (self.data_dir / "agentforge.db")

    @property
    def resolved_events_file(self) -> Path:
        return self.events_file or (self.data_dir / "events.jsonl")

    def ensure_directories(self) -> None:
        """Create the data/output directories required by the runtime."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.resolved_sqlite_file.parent.mkdir(parents=True, exist_ok=True)
        self.resolved_events_file.parent.mkdir(parents=True, exist_ok=True)
        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def mock_copy(self) -> "Settings":
        """Return a copy forced to the deterministic mock provider."""
        return self.model_copy(deep=True, update={"llm_provider": "mock"})

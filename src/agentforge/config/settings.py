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
    max_reassignments: int = Field(default=2, ge=0, le=10)
    max_execution_steps: int = Field(default=30, ge=1, le=200)
    max_tasks_per_workflow: int = Field(default=60, ge=1, le=500)
    enable_final_review: bool = True

    # --- Tools -----------------------------------------------------------------
    tools_enabled: bool = True
    max_tool_calls_per_task: int = Field(default=6, ge=1, le=30)
    tool_output_limit_bytes: int = Field(default=65_536, ge=1024)
    workspace_root: Path | None = None
    artifacts_root: Path | None = None
    max_file_bytes: int = Field(default=262_144, ge=1024, le=5_000_000)
    max_workspace_files: int = Field(default=200, ge=1, le=2000)

    # --- Human-in-the-loop -----------------------------------------------------
    approval_ttl_seconds: int = Field(default=3600, ge=10)
    clarification_ttl_seconds: int = Field(default=86400, ge=10)
    workflow_event_limit: int = Field(default=1000, ge=1)

    # --- Docker sandbox --------------------------------------------------------
    sandbox_image: str = "python:3.11-slim"
    sandbox_cpu_limit: float = Field(default=1.0, gt=0, le=16)
    sandbox_memory_mb: int = Field(default=256, ge=64, le=8192)
    sandbox_pids_limit: int = Field(default=64, ge=16, le=4096)
    sandbox_timeout_seconds: int = Field(default=30, ge=1, le=600)
    sandbox_output_limit_bytes: int = Field(default=65_536, ge=1024)
    sandbox_network_enabled: bool = False
    sandbox_container_user: str = "65534:65534"
    sandbox_read_only_root: bool = True
    sandbox_tmpfs_size_mb: int = Field(default=64, ge=8, le=1024)

    # --- Search ----------------------------------------------------------------
    search_provider: Literal["mock", "http"] = "mock"
    search_endpoint: str | None = None
    search_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENTFORGE_SEARCH_API_KEY", "SEARCH_API_KEY"),
    )
    search_timeout: float = Field(default=15.0, gt=0)
    search_max_results: int = Field(default=5, ge=1, le=10)
    web_fetch_max_bytes: int = Field(default=262_144, ge=1024, le=1_048_576)
    web_fetch_max_redirects: int = Field(default=3, ge=0, le=10)

    # --- Local Git tools -------------------------------------------------------
    git_repo_root: Path | None = None
    git_timeout: float = Field(default=30.0, gt=0)

    # --- Memory evolution (Phase 3) -------------------------------------------
    memory_evolution_enabled: bool = True
    memory_recall_enabled: bool = True
    memory_file: Path | None = None
    memory_recall_limit: int = Field(default=6, ge=1, le=20)
    memory_prompt_budget_chars: int = Field(default=3000, ge=100, le=20_000)
    memory_include_mock: bool = False
    memory_include_unverified: bool = False
    memory_promotion_threshold: int = Field(default=2, ge=1, le=10)
    memory_promotion_confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    memory_decay_half_life_days: float = Field(default=30.0, gt=0)
    memory_forget_threshold: float = Field(default=0.05, ge=0.0, le=1.0)
    memory_working_ttl_seconds: int = Field(default=3600, ge=1)
    memory_embedding_dimensions: int = Field(default=256, ge=16, le=4096)
    memory_store_mock_procedures: bool = True

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

    @property
    def resolved_workspace_root(self) -> Path:
        return self.workspace_root or (self.data_dir / "workspaces")

    @property
    def resolved_artifacts_root(self) -> Path:
        return self.artifacts_root or (self.data_dir / "artifacts")

    @property
    def resolved_memory_file(self) -> Path:
        return self.memory_file or (self.data_dir / "memory_evolution.db")

    def ensure_directories(self) -> None:
        """Create the data/output directories required by the runtime."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.resolved_sqlite_file.parent.mkdir(parents=True, exist_ok=True)
        self.resolved_events_file.parent.mkdir(parents=True, exist_ok=True)
        self.resolved_workspace_root.mkdir(parents=True, exist_ok=True)
        self.resolved_artifacts_root.mkdir(parents=True, exist_ok=True)
        self.resolved_memory_file.parent.mkdir(parents=True, exist_ok=True)
        if self.log_file is not None:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def mock_copy(self) -> "Settings":
        """Return a copy forced to the deterministic mock provider."""
        return self.model_copy(deep=True, update={"llm_provider": "mock"})

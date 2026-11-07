"""LLM provider factory."""

from __future__ import annotations

from agentforge.config.settings import Settings
from agentforge.llm.base import (
    BaseLLMProvider,
    ProviderConfigurationError,
)
from agentforge.llm.deepseek import DeepSeekProvider
from agentforge.llm.hkgai import HKGAIProvider
from agentforge.llm.mock import MockProvider


def build_llm_provider(
    settings: Settings, *, provider_name: str | None = None
) -> BaseLLMProvider:
    """Construct the configured provider.

    Missing credentials produce an explicit configuration error rather than a
    silent fallback, so real-API runs can never be mistaken for mock runs.
    """
    selected = (provider_name or settings.llm_provider).strip().lower()

    common = {
        "default_temperature": settings.temperature,
        "default_max_tokens": settings.max_tokens,
        "timeout": settings.request_timeout,
        "max_retries": settings.max_retries,
    }

    if selected == "mock":
        return MockProvider(
            latency_ms=settings.mock_latency_ms,
            default_temperature=settings.temperature,
            default_max_tokens=settings.max_tokens,
        )

    if selected == "deepseek":
        if settings.deepseek_api_key is None:
            raise ProviderConfigurationError(
                "DeepSeek provider selected but no API key is configured. "
                "Set AGENTFORGE_DEEPSEEK_API_KEY or DEEPSEEK_API_KEY in .env.",
                provider="deepseek",
            )
        api_key = settings.deepseek_api_key.get_secret_value()
        if not api_key.strip():
            raise ProviderConfigurationError(
                "DeepSeek API key is empty.",
                provider="deepseek",
            )
        return DeepSeekProvider(
            api_key=api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            **common,
        )

    if selected == "hkgai":
        if settings.hkgai_api_key is None:
            raise ProviderConfigurationError(
                "HKGAI provider selected but no API key is configured. "
                "Set AGENTFORGE_HKGAI_API_KEY or HKGAI_API_KEY in .env.",
                provider="hkgai",
            )
        api_key = settings.hkgai_api_key.get_secret_value()
        if not api_key.strip():
            raise ProviderConfigurationError(
                "HKGAI API key is empty.", provider="hkgai"
            )
        if not settings.hkgai_base_url or not settings.hkgai_model:
            raise ProviderConfigurationError(
                "HKGAI provider requires AGENTFORGE_HKGAI_BASE_URL and "
                "AGENTFORGE_HKGAI_MODEL. Confirm them with the course provider.",
                provider="hkgai",
            )
        return HKGAIProvider(
            api_key=api_key,
            base_url=settings.hkgai_base_url,
            model=settings.hkgai_model,
            **common,
        )

    raise ProviderConfigurationError(
        f"Unknown LLM provider {selected!r}; expected mock, deepseek, or hkgai.",
        provider=selected,
    )

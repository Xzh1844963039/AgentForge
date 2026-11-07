"""HKGAI LLM provider.

HKGAI exposes an OpenAI-compatible endpoint, but the exact course-provided base
URL and model name must be confirmed before real-API validation. No default URL
is invented here: both ``base_url`` and ``model`` are required.
"""

from __future__ import annotations

import httpx

from agentforge.llm.openai_compatible import OpenAICompatibleProvider


class HKGAIProvider(OpenAICompatibleProvider):
    """HKGAI chat completions provider (OpenAI-compatible protocol)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        default_temperature: float = 0.2,
        default_max_tokens: int = 2048,
        timeout: float = 60.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            name="hkgai",
            api_key=api_key,
            base_url=base_url,
            model=model,
            default_temperature=default_temperature,
            default_max_tokens=default_max_tokens,
            timeout=timeout,
            max_retries=max_retries,
            client=client,
        )

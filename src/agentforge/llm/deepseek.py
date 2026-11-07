"""DeepSeek LLM provider."""

from __future__ import annotations

import httpx

from agentforge.llm.openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek's OpenAI-compatible chat completions API."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        default_temperature: float = 0.2,
        default_max_tokens: int = 2048,
        timeout: float = 60.0,
        max_retries: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            name="deepseek",
            api_key=api_key,
            base_url=base_url,
            model=model,
            default_temperature=default_temperature,
            default_max_tokens=default_max_tokens,
            timeout=timeout,
            max_retries=max_retries,
            client=client,
        )

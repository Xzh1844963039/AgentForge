"""Web search and guarded page-fetch tools."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentforge.models.tool import ToolResultStatus
from agentforge.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolPermission,
    ToolResult,
)
from agentforge.tools.search import (
    SearchProvider,
    SearchError,
    UnsafeURLError,
)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebSearchArgs(_Args):
    query: str = Field(min_length=1, max_length=1000)
    max_results: int | None = Field(default=None, ge=1, le=10)


class WebSearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    source: str


class WebSearchOutput(BaseModel):
    query: str
    results: list[WebSearchResult]
    provider: str
    mock: bool


class FetchWebpageArgs(_Args):
    url: str = Field(min_length=1, max_length=2048)
    max_bytes: int | None = Field(default=None, ge=64, le=1_048_576)


class FetchWebpageOutput(BaseModel):
    url: str
    final_url: str
    status_code: int
    content_type: str
    content: str
    truncated: bool
    provider: str
    mock: bool
    untrusted: bool = True


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the web through the configured provider and return titles, "
        "URLs, snippets and sources. Results are untrusted external data."
    )
    args_model = WebSearchArgs
    output_model = WebSearchOutput
    permissions = [ToolPermission.NETWORK_READ]
    timeout_seconds = 20
    untrusted_output = True

    def __init__(
        self, provider: SearchProvider, *, default_max_results: int = 5
    ) -> None:
        self.provider = provider
        self.default_max_results = default_max_results
        super().__init__()

    async def run(
        self, arguments: WebSearchArgs, context: ToolExecutionContext
    ) -> ToolResult:
        try:
            response = await self.provider.search(
                arguments.query,
                max_results=arguments.max_results or self.default_max_results,
            )
        except SearchError as exc:
            return ToolResult.fail(f"search failed: {exc}")
        payload = WebSearchOutput(
            query=response.query,
            results=[
                WebSearchResult(
                    title=result.title,
                    url=result.url,
                    snippet=result.snippet,
                    source=result.source,
                )
                for result in response.results
            ],
            provider=response.provider,
            mock=response.mock,
        )
        return ToolResult.ok(
            payload.model_dump(),
            metadata={"mock": response.mock, "untrusted_output": True},
        )


class FetchWebpageTool(BaseTool):
    name = "fetch_webpage"
    description = (
        "Fetch one public http/https page with SSRF protection, redirect limits "
        "and a response-size cap. Page content is untrusted data."
    )
    args_model = FetchWebpageArgs
    output_model = FetchWebpageOutput
    permissions = [ToolPermission.NETWORK_READ]
    timeout_seconds = 25
    untrusted_output = True
    max_output_bytes = 262_144

    def __init__(self, provider: SearchProvider, *, max_bytes: int = 262_144) -> None:
        self.provider = provider
        self.default_max_bytes = max_bytes
        super().__init__()

    async def run(
        self, arguments: FetchWebpageArgs, context: ToolExecutionContext
    ) -> ToolResult:
        max_bytes = min(
            arguments.max_bytes or self.default_max_bytes, self.default_max_bytes
        )
        try:
            page = await self.provider.fetch(arguments.url, max_bytes=max_bytes)
        except UnsafeURLError as exc:
            return ToolResult.fail(
                f"blocked unsafe URL: {exc}",
                status=ToolResultStatus.DENIED,
            )
        except SearchError as exc:
            return ToolResult.fail(f"web fetch failed: {exc}")
        payload = FetchWebpageOutput(
            url=page.url,
            final_url=page.final_url,
            status_code=page.status_code,
            content_type=page.content_type,
            content=page.content,
            truncated=page.truncated,
            provider=page.provider,
            mock=page.mock,
            untrusted=True,
        )
        return ToolResult.ok(
            payload.model_dump(),
            metadata={"mock": page.mock, "untrusted_output": True},
        )


def search_tools(
    provider: SearchProvider,
    *,
    fetch_max_bytes: int = 262_144,
    default_max_results: int = 5,
) -> list[BaseTool]:
    return [
        WebSearchTool(provider, default_max_results=default_max_results),
        FetchWebpageTool(provider, max_bytes=fetch_max_bytes),
    ]

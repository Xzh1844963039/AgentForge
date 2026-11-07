"""Search providers and SSRF protection tests (no live network)."""

from __future__ import annotations


import httpx
import pytest
import pytest_asyncio

from agentforge.models.tool import ToolResultStatus
from agentforge.tools.base import ToolExecutionContext
from agentforge.tools.search import (
    HttpSearchProvider,
    MockSearchProvider,
    SearchError,
    UnsafeURLError,
    URLGuard,
    build_search_provider,
)
from agentforge.tools.search_tools import FetchWebpageTool, WebSearchTool


def public_resolver(*addresses: str):
    async def resolver(host: str, port: int) -> list[str]:
        return list(addresses or ("93.184.216.34",))

    return resolver


def context() -> ToolExecutionContext:
    return ToolExecutionContext(agent_id="research", workflow_id="wf", task_id="task")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1/admin",
        "http://10.1.2.3/internal",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/computeMetadata/v1",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://example.com:8080/",
        "http://user:password@example.com/",
    ],
)
async def test_url_guard_blocks_unsafe_urls(url):
    guard = URLGuard(public_resolver())
    with pytest.raises(UnsafeURLError):
        await guard.validate(url)


async def test_url_guard_blocks_private_dns_resolution():
    guard = URLGuard(public_resolver("10.0.0.5"))
    with pytest.raises(UnsafeURLError, match="non-public"):
        await guard.validate("https://looks-public.example/page")


async def test_url_guard_allows_public_url():
    guard = URLGuard(public_resolver("93.184.216.34"))
    normalized = await guard.validate("HTTPS://example.com/path?q=1#fragment")
    assert normalized == "https://example.com/path?q=1"


async def test_mock_search_is_labelled_and_offline():
    provider = MockSearchProvider()
    response = await provider.search("multi-agent systems", max_results=2)
    assert response.mock is True
    assert response.provider == "mock-search"
    assert len(response.results) == 2
    assert all(result.url.startswith("https://example.invalid/") for result in response.results)

    page = await provider.fetch("https://example.invalid/page")
    assert page.mock is True
    assert "MOCK WEBPAGE CONTENT" in page.content


async def test_web_search_tool_marks_output_untrusted():
    tool = WebSearchTool(MockSearchProvider())
    result = await tool.execute(
        {"query": "multi-agent", "max_results": 2}, context()
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.output["mock"] is True
    assert len(result.output["results"]) == 2
    assert result.metadata["untrusted_output"] is True


async def test_fetch_tool_marks_untrusted_and_truncates():
    tool = FetchWebpageTool(MockSearchProvider(), max_bytes=64)
    result = await tool.execute(
        {"url": "https://example.invalid/page", "max_bytes": 64}, context()
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.output["untrusted"] is True
    assert result.output["truncated"] is True
    assert len(result.output["content"].encode("utf-8")) <= 64


async def test_fetch_tool_denies_ssrf():
    class BlockingProvider(MockSearchProvider):
        async def fetch(self, url, *, max_bytes=262_144):
            raise UnsafeURLError("blocked hostname: localhost")

    tool = FetchWebpageTool(BlockingProvider())
    result = await tool.execute({"url": "http://localhost/x"}, context())
    assert result.status is ToolResultStatus.DENIED
    assert "blocked unsafe URL" in (result.error or "")


@pytest_asyncio.fixture
async def http_provider():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Result A",
                            "url": "https://example.com/a",
                            "snippet": "Snippet A",
                            "source": "test-search",
                        }
                    ]
                },
            )
        if request.url.path == "/redirect":
            return httpx.Response(
                302, headers={"location": "https://example.com/final"}
            )
        if request.url.path == "/bad-redirect":
            return httpx.Response(
                302, headers={"location": "http://127.0.0.1/secret"}
            )
        if request.url.path == "/large":
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                content=b"x" * 10_000,
            )
        return httpx.Response(
            200, headers={"content-type": "text/plain"}, content=b"hello world"
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = HttpSearchProvider(
        endpoint="https://search.example.com/search",
        timeout=5,
        guard=URLGuard(public_resolver()),
        client=client,
    )
    yield provider
    await provider.aclose()


async def test_http_search_provider(http_provider):
    response = await http_provider.search("query", max_results=5)
    assert response.mock is False
    assert response.results[0].title == "Result A"
    assert response.results[0].source == "test-search"


async def test_http_fetch_and_size_limit(http_provider):
    page = await http_provider.fetch("https://example.com/page", max_bytes=5)
    assert page.status_code == 200
    assert page.content == "hello"
    assert page.truncated is True

    large = await http_provider.fetch("https://example.com/large", max_bytes=32)
    assert large.truncated is True
    assert len(large.content.encode("utf-8")) <= 32


async def test_http_fetch_follows_valid_redirect(http_provider):
    page = await http_provider.fetch("https://example.com/redirect")
    assert page.final_url == "https://example.com/final"


async def test_http_fetch_rejects_redirect_to_private(http_provider):
    with pytest.raises(UnsafeURLError):
        await http_provider.fetch("https://example.com/bad-redirect")


async def test_search_retry_then_error(monkeypatch):
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(500, text="down")

    async def no_sleep(delay):
        return None

    monkeypatch.setattr("agentforge.tools.search.asyncio.sleep", no_sleep)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = HttpSearchProvider(
        endpoint="https://search.example.com/search",
        max_retries=2,
        client=client,
    )
    with pytest.raises(SearchError, match="HTTP 500"):
        await provider.search("q")
    assert attempts["count"] == 3
    await provider.aclose()


def test_build_search_provider_configuration():
    mock = build_search_provider(provider="mock", endpoint=None, api_key=None)
    assert mock.is_mock
    with pytest.raises(SearchError, match="AGENTFORGE_SEARCH_ENDPOINT"):
        build_search_provider(provider="http", endpoint=None, api_key=None)
    with pytest.raises(SearchError, match="unknown search provider"):
        build_search_provider(provider="other", endpoint=None, api_key=None)

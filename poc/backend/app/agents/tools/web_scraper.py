import asyncio
import random

import httpx
from agno.tools import tool
from bs4 import BeautifulSoup
from markdownify import markdownify

# 429 gets exponential backoff that honors the server's Retry-After header when it sends one;
# other retryable statuses (4xx/5xx) get the same backoff without a header to consult — a flaky
# upstream occasionally answers a good URL with a bogus 400/500, and a bounded number of retries
# with growing delay is cheap insurance against that without hammering a genuinely-down site.
MAX_RETRIES = 4
INITIAL_BACKOFF_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2.0
MAX_BACKOFF_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 20.0
MAX_MARKDOWN_CHARS = 20_000  # keep a single fetched page from blowing the agent's context window

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _backoff_seconds(attempt: int, retry_after: str | None) -> float:
    """`attempt` is 0-indexed. Honors a numeric Retry-After header when the server sends one
    (standard practice for 429s); otherwise grows exponentially with jitter so several claims
    hitting the same rate-limited host at once don't retry in lockstep."""
    if retry_after is not None:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass  # Retry-After can also be an HTTP date — not worth parsing for this POC.
    delay = min(INITIAL_BACKOFF_SECONDS * (BACKOFF_MULTIPLIER**attempt), MAX_BACKOFF_SECONDS)
    return delay * (0.5 + random.random())


def _html_to_markdown(html: str) -> str:
    # markdownify's `strip` option removes a tag's wrapper but keeps its inner text, which for
    # <script>/<style> means the raw JS/CSS source leaks into the output — decompose those
    # elements outright first instead.
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()

    text = markdownify(str(soup), heading_style="ATX").strip()
    if len(text) > MAX_MARKDOWN_CHARS:
        text = text[:MAX_MARKDOWN_CHARS] + "\n\n... [truncated]"
    return text


async def scrape_url(url: str) -> str:
    """Fetches `url` with a plain HTTP GET (BeautifulSoup only — no browser/JS execution, so
    content that a page renders client-side after load won't be present in the response), retrying
    with exponential backoff on 429 (rate limited) and on other 4xx/5xx responses, up to
    MAX_RETRIES attempts, then returns the page as markdown."""
    last_status: int | None = None
    last_error: str | None = None

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=REQUEST_TIMEOUT_SECONDS, headers={"User-Agent": _USER_AGENT}
    ) as client:
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await client.get(url)
            except httpx.TimeoutException:
                last_error = f"timed out after {REQUEST_TIMEOUT_SECONDS}s"
                if attempt == MAX_RETRIES:
                    break
                await asyncio.sleep(_backoff_seconds(attempt, retry_after=None))
                continue
            except httpx.HTTPError as exc:
                last_error = str(exc)
                if attempt == MAX_RETRIES:
                    break
                await asyncio.sleep(_backoff_seconds(attempt, retry_after=None))
                continue

            status = response.status_code
            last_status = status
            if status == 429 or 400 <= status < 600:
                if attempt == MAX_RETRIES:
                    break
                retry_after = response.headers.get("retry-after") if status == 429 else None
                await asyncio.sleep(_backoff_seconds(attempt, retry_after))
                continue

            return _html_to_markdown(response.text)

    detail = f"HTTP {last_status}" if last_status is not None else (last_error or "unknown error")
    return f"ERROR: failed to fetch {url} after {MAX_RETRIES + 1} attempts ({detail})."


@tool(
    name="scrape_url",
    description=(
        "Fetch a URL over plain HTTP and return its content as markdown. Does not execute "
        "JavaScript, so client-rendered content won't be captured — best for static pages. "
        "Retries with exponential backoff on 429 rate-limit responses and on other 4xx/5xx "
        "errors before giving up."
    ),
)
async def scrape_url_tool(url: str) -> str:
    return await scrape_url(url)

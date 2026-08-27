import asyncio
import random

from agno.tools import tool
from bs4 import BeautifulSoup
from markdownify import markdownify
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Response, TimeoutError as PlaywrightTimeoutError, async_playwright

# 429 gets exponential backoff that honors the server's Retry-After header when it sends one;
# other retryable statuses (4xx/5xx) get the same backoff without a header to consult — a flaky
# upstream occasionally answers a good URL with a bogus 400/500, and a bounded number of retries
# with growing delay is cheap insurance against that without hammering a genuinely-down site.
MAX_RETRIES = 4
INITIAL_BACKOFF_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2.0
MAX_BACKOFF_SECONDS = 30.0
# JS-heavy pages (SPAs, ad-tech-laden news sites) can take a while to settle network activity;
# the overall per-claim budget is bounded separately by EXTERNAL_VERIFICATION_TIMEOUT_SECONDS in
# verify_claim.py, so a generous per-fetch ceiling here just avoids one slow page eating the
# whole retry budget on timeouts alone.
PAGE_LOAD_TIMEOUT_MS = 25_000
MAX_MARKDOWN_CHARS = 20_000  # keep a single fetched page from blowing the agent's context window

_playwright = None
_browser = None
_browser_lock = asyncio.Lock()


async def _get_browser():
    """Lazily launches one shared headless Chromium for the process — a fresh browser per scrape
    call would dwarf the actual page-fetch cost. Guarded by a lock so concurrent scrape calls
    (claims verified concurrently gather external evidence in parallel) can't race into launching
    two browsers."""
    global _playwright, _browser
    async with _browser_lock:
        if _browser is None:
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(headless=True)
    return _browser


async def close_scraper_browser() -> None:
    """Not wired into an app shutdown hook — this POC's FastAPI app has none yet. Call explicitly
    from tests, or a future lifespan handler, to release the browser process cleanly."""
    global _playwright, _browser
    async with _browser_lock:
        if _browser is not None:
            await _browser.close()
            _browser = None
        if _playwright is not None:
            await _playwright.stop()
            _playwright = None


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


async def _fetch_rendered_html(url: str) -> tuple[int | None, dict[str, str], str]:
    """Navigates to `url` and waits for the page to actually finish loading — including
    JS-driven content — before reading the DOM, so client-rendered pages (most modern sites)
    don't get scraped mid-skeleton the way a plain HTTP GET would."""
    browser = await _get_browser()
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    )
    try:
        page = await context.new_page()
        response: Response | None = await page.goto(url, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT_MS)
        # networkidle already implies the DOM has settled, but SPAs sometimes finish their last
        # fetch and then still patch the DOM on the next tick — a short grace period catches that
        # without materially slowing down static pages.
        await page.wait_for_timeout(500)
        html = await page.content()
        status = response.status if response else None
        headers = response.headers if response else {}
        return status, headers, html
    finally:
        await context.close()


async def scrape_url(url: str) -> str:
    """Fetches `url` with a real (headless) browser so JavaScript-rendered content is captured,
    retrying with exponential backoff on 429 (rate limited) and on other 4xx/5xx responses, up to
    MAX_RETRIES attempts, then returns the page as markdown."""
    last_status: int | None = None
    last_error: str | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            status, headers, html = await _fetch_rendered_html(url)
        except PlaywrightTimeoutError:
            last_error = f"timed out after {PAGE_LOAD_TIMEOUT_MS}ms"
            if attempt == MAX_RETRIES:
                break
            await asyncio.sleep(_backoff_seconds(attempt, retry_after=None))
            continue
        except PlaywrightError as exc:
            last_error = str(exc)
            if attempt == MAX_RETRIES:
                break
            await asyncio.sleep(_backoff_seconds(attempt, retry_after=None))
            continue

        last_status = status
        if status is not None and (status == 429 or 400 <= status < 600):
            if attempt == MAX_RETRIES:
                break
            retry_after = headers.get("retry-after") if status == 429 else None
            await asyncio.sleep(_backoff_seconds(attempt, retry_after))
            continue

        return _html_to_markdown(html)

    detail = f"HTTP {last_status}" if last_status is not None else (last_error or "unknown error")
    return f"ERROR: failed to fetch {url} after {MAX_RETRIES + 1} attempts ({detail})."


@tool(
    name="scrape_url",
    description=(
        "Fetch a URL with a real browser (so JavaScript-rendered content loads) and return its "
        "content as markdown. Retries with exponential backoff on 429 rate-limit responses and "
        "on other 4xx/5xx errors before giving up."
    ),
)
async def scrape_url_tool(url: str) -> str:
    return await scrape_url(url)

"""HTTP fetching with rate limiting, User-Agent, and robots.txt respect."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

# Identify ourselves honestly — many sites drop empty / bot-like UAs.
DEFAULT_USER_AGENT = (
    "BacklinkChecker/1.0 (+https://github.com/open-source/backlink-checker; "
    "local research tool; respectful crawler)"
)

DEFAULT_TIMEOUT = 15  # seconds
DEFAULT_DELAY = 1.5  # seconds between requests


@dataclass
class FetchResult:
    """Outcome of fetching a single URL."""

    url: str
    final_url: str
    status_code: Optional[int]
    content_type: str
    html: str
    error: Optional[str] = None
    redirected: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and self.status_code is not None and self.html != ""

    @property
    def status_label(self) -> str:
        if self.error:
            return f"error: {self.error}"
        if self.status_code is None:
            return "unknown"
        label = str(self.status_code)
        if self.redirected and self.final_url != self.url:
            label += f" → {self.final_url}"
        return label


class RobotsCache:
    """Per-host robots.txt cache using the stdlib RobotFileParser."""

    def __init__(
        self,
        user_agent: str,
        session: requests.Session,
        timeout: float,
        before_request=None,
    ):
        self.user_agent = user_agent
        self.session = session
        self.timeout = timeout
        self.before_request = before_request  # optional rate-limit hook
        self._parsers: dict[str, RobotFileParser] = {}
        self._fetch_failed: set[str] = set()

    def _robots_url(self, page_url: str) -> str:
        parsed = urlparse(page_url)
        return f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    def allowed(self, page_url: str) -> bool:
        """
        Return True if robots.txt allows fetching page_url for our User-Agent.
        If robots.txt cannot be fetched, we allow the request (common practice)
        but note that a failed fetch was recorded.
        """
        parsed = urlparse(page_url)
        host_key = f"{parsed.scheme}://{parsed.netloc}".lower()

        if host_key not in self._parsers and host_key not in self._fetch_failed:
            rp = RobotFileParser()
            robots_url = self._robots_url(page_url)
            try:
                if self.before_request:
                    self.before_request()
                resp = self.session.get(robots_url, timeout=self.timeout)
                if resp.status_code == 200 and resp.text:
                    rp.parse(resp.text.splitlines())
                else:
                    # No robots.txt / empty → allow everything
                    rp.parse([])
                self._parsers[host_key] = rp
            except requests.exceptions.RequestException:
                self._fetch_failed.add(host_key)
                return True

        if host_key in self._fetch_failed:
            return True

        return self._parsers[host_key].can_fetch(self.user_agent, page_url)


class Fetcher:
    """Rate-limited HTTP client that respects robots.txt."""

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        delay: float = DEFAULT_DELAY,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.user_agent = user_agent
        self.delay = delay
        self.timeout = timeout
        self._last_request_at = 0.0

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self.robots = RobotsCache(
            user_agent,
            self.session,
            timeout,
            before_request=self._rate_limit_and_mark,
        )

    def _rate_limit_and_mark(self) -> None:
        self._rate_limit()
        self._last_request_at = time.monotonic()

    def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def fetch(self, url: str, *, check_robots: bool = True) -> FetchResult:
        """Fetch a URL and return HTML when possible. Never raises."""
        if check_robots and not self.robots.allowed(url):
            return FetchResult(
                url=url,
                final_url=url,
                status_code=None,
                content_type="",
                html="",
                error="blocked by robots.txt",
            )

        self._rate_limit()
        self._last_request_at = time.monotonic()

        try:
            resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        except requests.exceptions.Timeout:
            return FetchResult(url, url, None, "", "", "timeout")
        except requests.exceptions.TooManyRedirects:
            return FetchResult(url, url, None, "", "", "too many redirects")
        except requests.exceptions.SSLError as exc:
            return FetchResult(url, url, None, "", "", f"SSL error: {exc}")
        except requests.exceptions.ConnectionError as exc:
            return FetchResult(url, url, None, "", "", f"connection error: {exc}")
        except requests.exceptions.RequestException as exc:
            return FetchResult(url, url, None, "", "", str(exc))

        content_type = (resp.headers.get("Content-Type") or "").lower()
        redirected = resp.history is not None and len(resp.history) > 0
        final_url = resp.url

        # Non-HTML responses are not useful for <a> scanning
        if "html" not in content_type and "xml" not in content_type:
            # Some servers omit Content-Type; sniff a bit of the body
            sniff = (resp.text or "")[:200].lower().lstrip()
            if not (sniff.startswith("<!doctype") or sniff.startswith("<html") or "<html" in sniff):
                return FetchResult(
                    url=url,
                    final_url=final_url,
                    status_code=resp.status_code,
                    content_type=content_type,
                    html="",
                    error=f"non-HTML content ({content_type or 'unknown'})",
                    redirected=redirected,
                )

        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=resp.status_code,
            content_type=content_type,
            html=resp.text or "",
            redirected=redirected,
        )


def fetch_with_playwright(url: str, user_agent: str, timeout_ms: int = 20000) -> FetchResult:
    """Backward-compatible wrapper — prefer playwright_render.render_page."""
    from .playwright_render import render_page

    return render_page(url, user_agent, timeout_ms=timeout_ms)
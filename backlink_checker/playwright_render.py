"""Playwright Chromium renderer — one browser session per scan job."""

from __future__ import annotations

import os
from typing import Optional


def playwright_status() -> dict:
    """Report whether Playwright + Chromium look installable (no cross-thread launch)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "available": False,
            "ready": False,
            "message": "Playwright missing — run: pip install playwright && playwright install chromium",
        }

    try:
        with sync_playwright() as p:
            exe = p.chromium.executable_path
        if exe and os.path.exists(exe):
            return {
                "available": True,
                "ready": True,
                "message": "Chromium ready - flip Deep JS render on",
            }
        return {
            "available": True,
            "ready": False,
            "message": "Chromium not installed — run: playwright install chromium",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": True,
            "ready": False,
            "message": f"Chromium not ready: {exc}. Run: playwright install chromium",
        }


class PlaywrightSession:
    """
    Reusable headless Chromium for one scan job (same thread).

    Usage::
        with PlaywrightSession(user_agent) as session:
            result = session.render(url)
    """

    def __init__(self, user_agent: str, timeout_ms: int = 25000):
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms
        self._playwright = None
        self._browser = None
        self.error: Optional[str] = None

    def __enter__(self) -> "PlaywrightSession":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.error = (
                "playwright not installed "
                "(pip install playwright && playwright install chromium)"
            )
            return self

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    # Required on Render / container hosts (no sandbox privileges)
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
        except Exception as exc:  # noqa: BLE001
            self.error = (
                f"Chromium launch failed: {exc}. Run: playwright install chromium"
            )
            self.close()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._playwright = None

    def render(self, url: str):
        from .fetch import FetchResult

        if self.error:
            return FetchResult(url, url, None, "", "", self.error)
        if not self._browser:
            return FetchResult(url, url, None, "", "", "Playwright session not started")

        context = None
        page = None
        try:
            context = self._browser.new_context(
                user_agent=self.user_agent,
                viewport={"width": 1366, "height": 900},
                java_script_enabled=True,
                ignore_https_errors=True,
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)

            try:
                response = page.goto(
                    url, wait_until="domcontentloaded", timeout=self.timeout_ms
                )
            except Exception as exc:
                return FetchResult(url, url, None, "", "", f"navigation failed: {exc}")

            try:
                page.wait_for_load_state("networkidle", timeout=min(12000, self.timeout_ms))
            except Exception:
                try:
                    page.wait_for_load_state("load", timeout=5000)
                except Exception:
                    pass

            _auto_scroll(page)
            page.wait_for_timeout(600)
            _dismiss_overlays(page)
            _click_load_more(page)

            html = page.content()
            final_url = page.url
            status = response.status if response else None

            return FetchResult(
                url=url,
                final_url=final_url,
                status_code=status,
                content_type="text/html",
                html=html or "",
                redirected=final_url.rstrip("/") != url.rstrip("/"),
            )
        except Exception as exc:  # noqa: BLE001
            return FetchResult(url, url, None, "", "", f"playwright error: {exc}")
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass
            try:
                if context:
                    context.close()
            except Exception:
                pass


def render_page(url: str, user_agent: str, *, timeout_ms: int = 25000, session=None):
    """Render one URL — uses *session* if provided, else a one-shot browser."""
    if session is not None:
        return session.render(url)

    with PlaywrightSession(user_agent, timeout_ms=timeout_ms) as sess:
        return sess.render(url)


def _auto_scroll(page) -> None:
    try:
        page.evaluate(
            """async () => {
              const delay = (ms) => new Promise((r) => setTimeout(r, ms));
              const height = () => document.body.scrollHeight;
              let prev = 0;
              for (let i = 0; i < 8; i++) {
                window.scrollBy(0, Math.max(window.innerHeight * 0.9, 500));
                await delay(180);
                const h = height();
                if (h <= prev && (window.scrollY + window.innerHeight) >= h - 20) break;
                prev = h;
              }
              window.scrollTo(0, 0);
              await delay(100);
            }"""
        )
    except Exception:
        pass


def _dismiss_overlays(page) -> None:
    selectors = [
        "button:has-text('Accept')",
        "button:has-text('Accept all')",
        "button:has-text('I agree')",
        "button:has-text('Got it')",
        "[aria-label='Close']",
        "button:has-text('Close')",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible(timeout=400):
                loc.click(timeout=800)
                page.wait_for_timeout(200)
                break
        except Exception:
            continue


def _click_load_more(page, max_clicks: int = 5) -> None:
    """Click Load more / Show more a few times to reveal older posts."""
    from .pagination import LOAD_MORE_SELECTORS

    clicks = 0
    while clicks < max_clicks:
        clicked = False
        for sel in LOAD_MORE_SELECTORS:
            try:
                loc = page.locator(sel).first
                if not loc.count():
                    continue
                if not loc.is_visible(timeout=500):
                    continue
                loc.click(timeout=1500)
                clicks += 1
                clicked = True
                try:
                    page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    page.wait_for_timeout(700)
                _auto_scroll(page)
                break
            except Exception:
                continue
        if not clicked:
            break

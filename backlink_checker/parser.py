"""Parse HTML for backlinks pointing at a given domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup

from .domain import href_points_to_domain, is_internal_link
from .fetch import Fetcher
from .playwright_render import render_page


# Heuristic signals that a page may rely on client-side rendering.
_JS_HINTS = (
    "id=\"__next\"",
    "id=\"root\"",
    "id=\"app\"",
    "ng-version=",
    "data-reactroot",
    "__NUXT__",
    "window.__INITIAL_STATE__",
    "<noscript>",
)


@dataclass
class BacklinkMatch:
    """One <a> tag that points at the user's domain."""

    href: str
    anchor_text: str
    follow_status: str  # "dofollow" | "nofollow"
    rel: str


@dataclass
class PageCheckResult:
    """Full result for checking one target page."""

    target_url: str
    backlink_found: bool
    matches: list[BacklinkMatch] = field(default_factory=list)
    http_status: str = ""
    js_rendered_suspected: bool = False
    used_playwright: bool = False
    error: Optional[str] = None
    notes: str = ""

    @property
    def primary_anchor(self) -> str:
        if not self.matches:
            return ""
        # Join unique anchor texts for compact table display
        texts = []
        seen = set()
        for m in self.matches:
            t = m.anchor_text.strip() or "(empty)"
            if t not in seen:
                seen.add(t)
                texts.append(t)
        return " | ".join(texts)

    @property
    def primary_follow(self) -> str:
        if not self.matches:
            return ""
        statuses = {m.follow_status for m in self.matches}
        if statuses == {"dofollow"}:
            return "dofollow"
        if statuses == {"nofollow"}:
            return "nofollow"
        return "mixed"


def _follow_status(rel: str | list | None) -> str:
    """Return 'nofollow' if rel contains nofollow, else 'dofollow'."""
    if rel is None:
        return "dofollow"
    if isinstance(rel, list):
        tokens = " ".join(rel).lower().split()
    else:
        tokens = str(rel).lower().replace(",", " ").split()
    return "nofollow" if "nofollow" in tokens else "dofollow"


def _anchor_text(tag) -> str:
    text = tag.get_text(" ", strip=True)
    if text:
        return text
    # Image links often have empty text — fall back to img alt / title
    img = tag.find("img")
    if img:
        return (img.get("alt") or img.get("title") or "[image]").strip()
    return (tag.get("title") or "").strip()


def looks_js_heavy(html: str) -> bool:
    """Cheap heuristic: page shell looks like a SPA / JS-rendered app."""
    if not html:
        return False
    lower = html.lower()
    # Few anchors + known SPA markers → likely JS-rendered
    soup = BeautifulSoup(html, "lxml")
    anchor_count = len(soup.find_all("a", href=True))
    has_hint = any(h.lower() in lower for h in _JS_HINTS)
    script_count = len(soup.find_all("script"))
    if anchor_count <= 2 and (has_hint or script_count >= 5):
        return True
    if has_hint and anchor_count <= 5:
        return True
    return False


def find_backlinks(html: str, page_url: str, my_domain: str) -> list[BacklinkMatch]:
    """Scan HTML for <a> tags whose href points at my_domain."""
    soup = BeautifulSoup(html, "lxml")
    matches: list[BacklinkMatch] = []
    seen: set[tuple[str, str, str]] = set()

    for tag in soup.find_all("a", href=True):
        absolute = href_points_to_domain(tag["href"], page_url, my_domain)
        if not absolute:
            continue
        rel = tag.get("rel")
        follow = _follow_status(rel)
        anchor = _anchor_text(tag)
        rel_str = " ".join(rel) if isinstance(rel, list) else (rel or "")
        key = (absolute, anchor, follow)
        if key in seen:
            continue
        seen.add(key)
        matches.append(
            BacklinkMatch(
                href=absolute,
                anchor_text=anchor,
                follow_status=follow,
                rel=rel_str,
            )
        )
    return matches


def extract_internal_links(html: str, page_url: str, root_host: str) -> list[str]:
    """Collect unique internal links for crawl mode."""
    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        absolute = is_internal_link(tag["href"], page_url, root_host)
        if absolute and absolute not in seen:
            seen.add(absolute)
            found.append(absolute)
    return found


@dataclass
class PageCheckBundle:
    """Result plus HTML, so crawl mode can discover links without re-fetching."""

    result: PageCheckResult
    html: str = ""
    final_url: str = ""


def check_page(
    target_url: str,
    my_domain: str,
    fetcher: Fetcher,
    *,
    use_js: bool = False,
    on_progress=None,
    js_session=None,
) -> PageCheckResult:
    """Fetch *target_url* and look for backlinks to *my_domain*."""
    return check_page_bundle(
        target_url,
        my_domain,
        fetcher,
        use_js=use_js,
        on_progress=on_progress,
        js_session=js_session,
    ).result


def check_page_bundle(
    target_url: str,
    my_domain: str,
    fetcher: Fetcher,
    *,
    use_js: bool = False,
    on_progress=None,
    js_session=None,
) -> PageCheckBundle:
    """
    Fetch *target_url* and look for backlinks to *my_domain*.

    If no matches are found in static HTML and *use_js* is True, retry with
    Playwright Chromium (deep render). If Playwright is off but the page looks
    JS-heavy, set js_rendered_suspected so the UI can hint to enable deep render.
    """
    fetched = fetcher.fetch(target_url)
    if fetched.error and not fetched.html:
        return PageCheckBundle(
            result=PageCheckResult(
                target_url=target_url,
                backlink_found=False,
                http_status=fetched.status_label,
                error=fetched.error,
                notes=fetched.error,
            )
        )

    page_url = fetched.final_url or target_url
    html = fetched.html
    matches = find_backlinks(html, page_url, my_domain)
    js_suspected = False
    used_playwright = False
    notes = ""

    if not matches:
        js_suspected = looks_js_heavy(html)
        if use_js:
            if on_progress:
                on_progress(
                    {
                        "event": "js_render",
                        "url": page_url,
                        "message": f"Deep render (Chromium): {page_url}",
                    }
                )
            pw = render_page(page_url, fetcher.user_agent, session=js_session)
            used_playwright = True
            if pw.error:
                notes = f"static miss → Playwright: {pw.error}"
                if js_suspected:
                    notes += " (page looks JS-rendered)"
            else:
                html = pw.html
                page_url = pw.final_url or page_url
                matches = find_backlinks(html, page_url, my_domain)
                if pw.status_code is not None:
                    fetched = pw
                if matches:
                    notes = "found after deep JS render"
                else:
                    notes = "no backlink after deep JS render"
                    js_suspected = js_suspected or looks_js_heavy(html)
        elif js_suspected:
            notes = "static HTML miss — page may need deep JS render"

    result = PageCheckResult(
        target_url=target_url,
        backlink_found=bool(matches),
        matches=matches,
        http_status=fetched.status_label,
        js_rendered_suspected=js_suspected and not matches,
        used_playwright=used_playwright,
        notes=notes,
    )
    return PageCheckBundle(result=result, html=html, final_url=page_url)

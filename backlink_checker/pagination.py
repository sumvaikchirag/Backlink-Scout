"""Discover blog pagination and “next page” URLs for deeper crawl coverage."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from .domain import canonicalize_url, is_internal_link

# Anchor text / aria labels that usually mean “go to older / next listing page”
_NEXT_TEXT = re.compile(
    r"^\s*("
    r"next( page)?|older( posts)?|previous entries|"
    r"load more|show more|view more|more posts|more articles|"
    r"›|»|→|>>|&raquo;|&rsaquo;"
    r")\s*$",
    re.I,
)

# Paths / query keys used by common CMS pagination
_PAGE_PATH = re.compile(r"/page/(\d+)/?$", re.I)
_PAGE_QUERY_KEYS = ("page", "paged", "pg", "pagenum", "page_num", "offset")


def extract_pagination_links(html: str, page_url: str, root_host: str) -> list[str]:
    """
    Find internal pagination URLs on a page:

    - <a rel="next"> / <link rel="next">
    - “Next”, “Older posts”, “Load more”, » …
    - hrefs with ?page=N, ?paged=N, /page/N/
    - synthesized page+1 when the current URL is already a list page
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    found: list[str] = []
    seen: set[str] = set()

    def add(href: str | None) -> None:
        if not href:
            return
        absolute = is_internal_link(href, page_url, root_host)
        if absolute and absolute not in seen:
            seen.add(absolute)
            found.append(absolute)

    # 1) Explicit rel=next (anchors + <link> in head)
    for tag in soup.find_all(["a", "link"], href=True):
        rel = tag.get("rel")
        tokens = []
        if isinstance(rel, list):
            tokens = [t.lower() for t in rel]
        elif rel:
            tokens = str(rel).lower().replace(",", " ").split()
        if "next" in tokens:
            add(tag["href"])

    # 2) Next / Older / Load more by visible text or aria-label
    for tag in soup.find_all("a", href=True):
        label = " ".join(
            filter(
                None,
                [
                    tag.get_text(" ", strip=True),
                    tag.get("aria-label"),
                    tag.get("title"),
                ],
            )
        )
        if label and _NEXT_TEXT.match(label):
            add(tag["href"])
        # class hints: pagination next
        classes = " ".join(tag.get("class") or []).lower()
        if any(
            tip in classes
            for tip in ("next", "pagination__next", "nav-next", "older", "load-more")
        ):
            add(tag["href"])

    # 3) Any internal href that looks like a page-N listing URL
    for tag in soup.find_all("a", href=True):
        absolute = is_internal_link(tag["href"], page_url, root_host)
        if absolute and _looks_like_pagination(absolute):
            if absolute not in seen:
                seen.add(absolute)
                found.append(absolute)

    # 4) If we are already on ?page=2 or /page/2/, also queue page+1
    nxt = synthesize_next_page(page_url)
    if nxt:
        absolute = is_internal_link(nxt, page_url, root_host)
        if absolute and absolute not in seen:
            # Only trust synthesis if the page showed *some* pagination signal
            if found or _looks_like_pagination(canonicalize_url(page_url)):
                seen.add(absolute)
                found.append(absolute)

    return found


def _looks_like_pagination(url: str) -> bool:
    parsed = urlparse(url)
    if _PAGE_PATH.search(parsed.path or ""):
        return True
    qs = parse_qs(parsed.query)
    for key in _PAGE_QUERY_KEYS:
        if key in qs:
            try:
                return int(qs[key][0]) >= 1
            except (TypeError, ValueError):
                return True
    return False


def synthesize_next_page(url: str) -> str | None:
    """Return page+1 URL for known pagination patterns, else None."""
    parsed = urlparse(url)
    path = parsed.path or "/"

    m = _PAGE_PATH.search(path)
    if m:
        n = int(m.group(1)) + 1
        new_path = _PAGE_PATH.sub(f"/page/{n}/", path)
        return urlunparse(
            (parsed.scheme, parsed.netloc, new_path, "", parsed.query, "")
        )

    qs = parse_qs(parsed.query, keep_blank_values=True)
    for key in _PAGE_QUERY_KEYS:
        if key in qs and qs[key]:
            try:
                n = int(qs[key][0]) + 1
            except (TypeError, ValueError):
                continue
            qs[key] = [str(n)]
            query = urlencode({k: v[0] if len(v) == 1 else v for k, v in qs.items()}, doseq=True)
            return urlunparse(
                (parsed.scheme, parsed.netloc, path, "", query, "")
            )

    return None


# Selectors clicked during Playwright deep-render to expand infinite lists
LOAD_MORE_SELECTORS = [
    "button:has-text('Load more')",
    "a:has-text('Load more')",
    "button:has-text('Show more')",
    "a:has-text('Show more')",
    "button:has-text('View more')",
    "button:has-text('More posts')",
    "[class*='load-more']",
    "[class*='loadmore']",
    "[data-load-more]",
]

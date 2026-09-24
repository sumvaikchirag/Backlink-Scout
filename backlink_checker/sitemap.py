"""Discover site URLs from robots.txt / sitemap.xml (better coverage than BFS alone)."""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from .domain import canonicalize_url, extract_registrable_host, normalize_host
from .fetch import Fetcher

# Tag clouds explode crawl size and rarely hold unique editorial backlinks.
_SKIP_SITEMAP_HINTS = (
    "post_tag",
    "product_tag",
    "image-sitemap",
    "video-sitemap",
)
_CONTENT_SITEMAP_HINTS = (
    "post-sitemap",
    "page-sitemap",
    "recipes-sitemap",
    "product-sitemap",
    "category-sitemap",
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_xml_locs(xml_text: str) -> tuple[list[str], list[str]]:
    """Return (child_sitemap_urls, page_urls) from a sitemap document."""
    sitemap_locs: list[str] = []
    url_locs: list[str] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml_text, flags=re.I):
            url_locs.append(loc.strip())
        return sitemap_locs, url_locs

    kind = _local(root.tag).lower()
    if kind == "sitemapindex":
        for el in root.iter():
            if _local(el.tag).lower() == "loc" and el.text and el.text.strip():
                sitemap_locs.append(el.text.strip())
    else:
        for el in root.iter():
            if _local(el.tag).lower() == "loc" and el.text and el.text.strip():
                url_locs.append(el.text.strip())
    return sitemap_locs, url_locs


def _should_skip_sitemap(url: str) -> bool:
    lower = url.lower()
    return any(h in lower for h in _SKIP_SITEMAP_HINTS)


def _sitemap_priority(url: str) -> int:
    lower = url.lower()
    for i, hint in enumerate(_CONTENT_SITEMAP_HINTS):
        if hint in lower:
            return i
    return 50 if "sitemap" in lower else 100


def discover_sitemaps_from_robots(site_url: str, fetcher: Fetcher) -> list[str]:
    """Read Sitemap: lines from robots.txt."""
    parsed = urlparse(site_url if "://" in site_url else "https://" + site_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    result = fetcher.fetch(robots_url, check_robots=False)
    text = result.html or ""
    if not text and result.status_code != 200:
        # Non-HTML rejection — pull raw body once
        try:
            fetcher._rate_limit()
            import time

            fetcher._last_request_at = time.monotonic()
            resp = fetcher.session.get(robots_url, timeout=fetcher.timeout)
            text = resp.text or ""
        except Exception:
            text = ""

    found: list[str] = []
    for line in text.splitlines():
        if line.lower().startswith("sitemap:"):
            loc = line.split(":", 1)[1].strip()
            if loc:
                found.append(loc)
    return found


def default_sitemap_candidates(site_url: str) -> list[str]:
    parsed = urlparse(site_url if "://" in site_url else "https://" + site_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/wp-sitemap.xml",
    ]


def collect_sitemap_urls(
    site_url: str,
    fetcher: Fetcher,
    *,
    max_urls: int = 2000,
    include_tag_sitemaps: bool = False,
    on_progress: Callable[[dict], None] | None = None,
) -> list[str]:
    """
    Collect page URLs from the site's sitemap(s).

    Prefers post/page/recipe sitemaps; skips tag sitemaps by default.
    """
    host = extract_registrable_host(site_url)
    seeds: list[str] = []
    for s in discover_sitemaps_from_robots(site_url, fetcher):
        seeds.append(s)
    for s in default_sitemap_candidates(site_url):
        if s not in seeds:
            seeds.append(s)

    queue: list[str] = list(seeds)
    seen_sitemaps: set[str] = set()
    page_urls: list[str] = []
    seen_pages: set[str] = set()

    def emit(msg: str) -> None:
        if on_progress:
            on_progress({"event": "sitemap", "message": msg, "urls": len(page_urls)})

    while queue and len(page_urls) < max_urls:
        sm_url = queue.pop(0)
        if sm_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sm_url)

        if not include_tag_sitemaps and _should_skip_sitemap(sm_url):
            emit(f"Skipping tag sitemap: {sm_url}")
            continue

        emit(f"Reading sitemap: {sm_url}")
        fetched = fetcher.fetch(sm_url)
        if not fetched.html:
            continue

        child_sitemaps, urls = _parse_xml_locs(fetched.html)
        child_sitemaps.sort(key=_sitemap_priority)
        for child in child_sitemaps:
            if child not in seen_sitemaps:
                queue.append(child)

        for loc in urls:
            loc_host = extract_registrable_host(loc)
            if loc_host != host and normalize_host(urlparse(loc).netloc) != normalize_host(host):
                continue
            canon = canonicalize_url(loc)
            if not canon or canon in seen_pages:
                continue
            lower = canon.lower()
            if any(
                lower.endswith(ext)
                for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".zip", ".css", ".js")
            ):
                continue
            seen_pages.add(canon)
            page_urls.append(canon)
            if len(page_urls) >= max_urls:
                break

    emit(f"Sitemap discovery found {len(page_urls)} page URL(s)")
    return page_urls

"""Crawl / full-site scan: sitemap discovery + optional BFS link following."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from urllib.parse import urlparse

from .domain import canonicalize_url, extract_registrable_host
from .fetch import Fetcher
from .pagination import extract_pagination_links
from .parser import PageCheckResult, check_page_bundle, extract_internal_links
from .report import result_to_dict
from .sitemap import collect_sitemap_urls

ProgressCallback = Callable[[dict], None]
CancelCallback = Callable[[], bool]


def crawl_and_check(
    start_url: str,
    my_domain: str,
    fetcher: Fetcher,
    *,
    max_depth: int = 2,
    max_pages: int = 25,
    use_js: bool = False,
    use_sitemap: bool = True,
    follow_links: bool = True,
    js_session=None,
    on_progress: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> list[PageCheckResult]:
    """
    Check pages on *start_url*'s site for backlinks to *my_domain*.

    By default, seeds the queue from sitemap.xml (so blog posts are found),
    then optionally follows internal links up to *max_depth*.
    """
    root_host = extract_registrable_host(start_url)
    if not root_host:
        return [
            PageCheckResult(
                target_url=start_url,
                backlink_found=False,
                error="invalid start URL",
                notes="invalid start URL",
                http_status="error: invalid start URL",
            )
        ]

    parsed = urlparse(start_url if "://" in start_url else "https://" + start_url)
    seed = canonicalize_url(parsed.geturl())

    # queue items: (url, depth) — sitemap seeds use depth 0
    queue: deque[tuple[str, int]] = deque()
    queued: set[str] = set()

    def enqueue(url: str, depth: int) -> None:
        if url and url not in queued:
            queued.add(url)
            queue.append((url, depth))

    def emit(event: str, **extra) -> None:
        if on_progress:
            on_progress(
                {
                    "event": event,
                    "checked": len(results),
                    "queued": len(queue),
                    "found": found_count,
                    "max_pages": max_pages,
                    **extra,
                }
            )

    results: list[PageCheckResult] = []
    found_count = 0
    visited: set[str] = set()

    emit("started", url=seed, message=f"Starting full-site scan of {root_host}")

    if use_sitemap:
        emit("sitemap", message="Discovering pages from sitemap.xml…", url=seed)

        def sm_progress(payload: dict) -> None:
            emit(
                "sitemap",
                message=payload.get("message", ""),
                url=seed,
            )

        sitemap_urls = collect_sitemap_urls(
            seed,
            fetcher,
            max_urls=max(max_pages * 5, 500),
            on_progress=sm_progress,
        )
        # Homepage first, then sitemap pages
        enqueue(seed, 0)
        for u in sitemap_urls:
            enqueue(u, 0)
        emit(
            "sitemap",
            message=f"Queued {len(queued)} URL(s) from sitemap + homepage",
            url=seed,
        )
    else:
        enqueue(seed, 0)

    while queue and len(results) < max_pages:
        if should_cancel and should_cancel():
            emit("cancelled", message="Scan cancelled")
            break

        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        emit("checking", url=url, depth=depth, message=f"Checking {url}")

        def page_progress(payload: dict) -> None:
            emit(
                payload.get("event", "js_render"),
                url=payload.get("url", url),
                depth=depth,
                message=payload.get("message", ""),
            )

        bundle = check_page_bundle(
            url,
            my_domain,
            fetcher,
            use_js=use_js,
            on_progress=page_progress if use_js else None,
            js_session=js_session,
        )
        results.append(bundle.result)
        if bundle.result.backlink_found:
            found_count += 1

        emit(
            "page",
            url=url,
            depth=depth,
            backlink=bundle.result.backlink_found,
            status=bundle.result.http_status,
            result=result_to_dict(bundle.result),
        )

        if follow_links and bundle.html:
            base = bundle.final_url or url
            # Pagination stays at the same depth so page/2 → page/3 keeps going
            for link in extract_pagination_links(bundle.html, base, root_host):
                enqueue(link, depth)
            if depth < max_depth:
                for link in extract_internal_links(bundle.html, base, root_host):
                    enqueue(link, depth + 1)

    emit(
        "done",
        message=f"Finished: {found_count}/{len(results)} page(s) have your backlink",
    )
    return results

"""URL / domain normalization helpers for reliable backlink matching."""

from __future__ import annotations

from urllib.parse import urljoin, urlparse, urlunparse


def normalize_host(host: str | None) -> str:
    """Lowercase hostname and strip a leading 'www.' for comparison."""
    if not host:
        return ""
    host = host.lower().strip().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def extract_registrable_host(url: str) -> str:
    """Return the normalized host from a URL (www stripped)."""
    parsed = urlparse(url.strip())
    return normalize_host(parsed.netloc or parsed.path.split("/")[0])


def canonicalize_url(url: str) -> str:
    """
    Normalize a URL for deduplication:
    - force https scheme if missing
    - lowercase host
    - strip fragment
    - strip trailing slash (except bare domain root kept consistent)
    """
    url = url.strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)
    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "").lower()
    path = parsed.path or "/"
    # Collapse empty trailing slash differences for non-root paths
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def is_same_site(url_a: str, url_b: str) -> bool:
    """True if both URLs share the same normalized host (www-insensitive)."""
    return extract_registrable_host(url_a) == extract_registrable_host(url_b)


def href_points_to_domain(href: str, page_url: str, my_domain: str) -> str | None:
    """
    Resolve *href* against *page_url* and return the absolute URL if it
    points at *my_domain* (http/https, www/non-www, any path). Otherwise None.
    """
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None

    absolute = urljoin(page_url, href.strip())
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https"):
        return None

    link_host = normalize_host(parsed.netloc)
    target_host = normalize_host(my_domain)
    # Also accept my_domain passed as a full URL
    if "://" in my_domain or "/" in my_domain:
        target_host = extract_registrable_host(my_domain)

    if not link_host or not target_host:
        return None
    if link_host == target_host or link_host.endswith("." + target_host):
        # Match exact domain or subdomains of the checked site
        # (e.g. blog.mysite.com when checking mysite.com)
        return absolute
    return None


def is_internal_link(href: str, page_url: str, root_host: str) -> str | None:
    """Return absolute URL if href is an internal link on root_host."""
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    absolute = urljoin(page_url, href.strip())
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https"):
        return None
    if normalize_host(parsed.netloc) != normalize_host(root_host):
        return None
    return canonicalize_url(absolute)

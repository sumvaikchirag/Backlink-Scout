"""Command-line interface for the backlink checker."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .crawl import crawl_and_check
from .domain import extract_registrable_host
from .fetch import DEFAULT_DELAY, DEFAULT_USER_AGENT, Fetcher
from .parser import check_page
from .report import export_csv, print_table


def _load_targets(args: argparse.Namespace) -> list[str]:
    """Collect target URLs from CLI args and/or an input file."""
    targets: list[str] = []

    if args.targets:
        # Allow space-separated and also a single multi-line string
        for item in args.targets:
            for line in item.replace(",", "\n").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    targets.append(line)

    if args.file:
        path = Path(args.file)
        if not path.is_file():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                targets.append(line)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in targets:
        key = t.rstrip("/")
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return unique


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backlink-checker",
        description=(
            "Free, open-source backlink checker. Fetches target pages and "
            "scans for <a> links pointing at your domain. No paid APIs."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  # Check one page
  python -m backlink_checker --me https://mysite.com --targets https://example.com/blog/post

  # Check a list of URLs from a file
  python -m backlink_checker --me mysite.com --file urls.txt --output results.csv

  # Crawl a site (depth 2, up to 30 pages) looking for your link
  python -m backlink_checker --me https://mysite.com --crawl https://directory.example \\
      --max-depth 2 --max-pages 30 --output crawl.csv

  # Retry with Playwright when static HTML finds nothing
  python -m backlink_checker --me mysite.com --targets https://spa.example --js
""".strip(),
    )
    p.add_argument(
        "--me",
        "--my-site",
        dest="my_site",
        required=True,
        help="Your website URL or domain (e.g. https://mysite.com or mysite.com)",
    )
    p.add_argument(
        "--targets",
        "-t",
        nargs="+",
        help="One or more target page URLs to check",
    )
    p.add_argument(
        "--file",
        "-f",
        help="Path to a text file with one target URL per line",
    )
    p.add_argument(
        "--crawl",
        metavar="URL",
        help="Crawl this site (follow internal links) and check every page found",
    )
    p.add_argument(
        "--max-depth",
        type=int,
        default=2,
        help="Max crawl depth from the start URL (default: 2)",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=200,
        help="Max pages to visit in crawl mode (default: 200)",
    )
    p.add_argument(
        "--no-sitemap",
        action="store_true",
        help="Do not seed from sitemap.xml (homepage link-follow only)",
    )
    p.add_argument(
        "--js",
        action="store_true",
        help="If static HTML has no match, re-check with Playwright (Chromium)",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Seconds to wait between requests (default: {DEFAULT_DELAY})",
    )
    p.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="Custom User-Agent string",
    )
    p.add_argument(
        "--output",
        "-o",
        default="backlink_results.csv",
        help="CSV output path (default: backlink_results.csv)",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    my_host = extract_registrable_host(args.my_site)
    if not my_host:
        print("Error: could not parse --me domain.", file=sys.stderr)
        return 1

    if not args.crawl and not args.targets and not args.file:
        parser.error("Provide --targets and/or --file, or use --crawl URL")

    fetcher = Fetcher(
        user_agent=args.user_agent,
        delay=max(0.0, args.delay),
    )

    print(f"Looking for backlinks to: {my_host}")
    print(f"User-Agent: {args.user_agent}")
    print(f"Request delay: {args.delay}s")
    if args.js:
        print("JS fallback: enabled (Playwright)")
    print()

    results = []

    if args.crawl:
        print(
            f"Crawl mode: {args.crawl} "
            f"(depth={args.max_depth}, max_pages={args.max_pages})"
        )
        results.extend(
            crawl_and_check(
                args.crawl,
                my_host,
                fetcher,
                max_depth=args.max_depth,
                max_pages=args.max_pages,
                use_js=args.js,
                use_sitemap=not args.no_sitemap,
            )
        )

    targets = _load_targets(args)
    if targets:
        print(f"Checking {len(targets)} target URL(s)…")
        for i, url in enumerate(targets, 1):
            print(f"  [{i}/{len(targets)}] {url}")
            results.append(check_page(url, my_host, fetcher, use_js=args.js))

    if not results:
        print("Nothing to check.", file=sys.stderr)
        return 1

    print_table(results)
    out = export_csv(results, args.output)
    print(f"CSV written to: {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

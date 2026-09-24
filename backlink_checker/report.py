"""Table printing and CSV export for check results."""

from __future__ import annotations

import csv
from pathlib import Path

from tabulate import tabulate

from .parser import PageCheckResult


def result_to_dict(r: PageCheckResult) -> dict:
    """Serialize a page result for the web UI / JSON API."""
    notes = r.notes
    if r.js_rendered_suspected and "JS" not in notes:
        notes = (notes + "; " if notes else "") + "may be JS-rendered"
    if r.used_playwright:
        notes = (notes + "; " if notes else "") + "deep JS render"
    return {
        "url": r.target_url,
        "backlink": r.backlink_found,
        "anchor_text": r.primary_anchor,
        "follow": r.primary_follow,
        "http_status": r.http_status,
        "js_suspected": r.js_rendered_suspected,
        "used_playwright": r.used_playwright,
        "notes": notes,
        "error": r.error or "",
        "matches": [
            {
                "href": m.href,
                "anchor_text": m.anchor_text,
                "follow": m.follow_status,
            }
            for m in r.matches
        ],
    }


def results_to_rows(results: list[PageCheckResult]) -> list[dict[str, str]]:
    """Flatten results into one row per page (primary match summary)."""
    rows: list[dict[str, str]] = []
    for r in results:
        d = result_to_dict(r)
        rows.append(
            {
                "URL": d["url"],
                "Backlink": "Y" if d["backlink"] else "N",
                "Anchor text": d["anchor_text"],
                "Follow": d["follow"],
                "HTTP status": d["http_status"],
                "Notes": d["notes"],
            }
        )
    return rows


def print_table(results: list[PageCheckResult]) -> None:
    """Print a clean console table of results."""
    rows = results_to_rows(results)
    if not rows:
        print("No results.")
        return

    table = tabulate(
        [
            [
                row["URL"],
                row["Backlink"],
                row["Anchor text"][:60] + ("…" if len(row["Anchor text"]) > 60 else ""),
                row["Follow"],
                row["HTTP status"],
                row["Notes"][:50] + ("…" if len(row["Notes"]) > 50 else ""),
            ]
            for row in rows
        ],
        headers=["URL", "Backlink", "Anchor text", "Follow", "HTTP status", "Notes"],
        tablefmt="github",
        maxcolwidths=[48, 8, 40, 10, 28, 36],
    )
    print()
    print(table)
    print()

    found = sum(1 for r in results if r.backlink_found)
    print(f"Summary: {found}/{len(results)} page(s) contain a backlink.")


def export_csv(results: list[PageCheckResult], path: str | Path) -> Path:
    """Write results to CSV. Also expands one row per individual match when useful."""
    path = Path(path)
    fieldnames = [
        "URL",
        "Backlink",
        "Anchor text",
        "Follow",
        "Matched href",
        "HTTP status",
        "JS suspected",
        "Used Playwright",
        "Notes",
        "Error",
    ]

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            if r.matches:
                for m in r.matches:
                    writer.writerow(
                        {
                            "URL": r.target_url,
                            "Backlink": "Y",
                            "Anchor text": m.anchor_text,
                            "Follow": m.follow_status,
                            "Matched href": m.href,
                            "HTTP status": r.http_status,
                            "JS suspected": "Y" if r.js_rendered_suspected else "N",
                            "Used Playwright": "Y" if r.used_playwright else "N",
                            "Notes": r.notes,
                            "Error": r.error or "",
                        }
                    )
            else:
                writer.writerow(
                    {
                        "URL": r.target_url,
                        "Backlink": "N",
                        "Anchor text": "",
                        "Follow": "",
                        "Matched href": "",
                        "HTTP status": r.http_status,
                        "JS suspected": "Y" if r.js_rendered_suspected else "N",
                        "Used Playwright": "Y" if r.used_playwright else "N",
                        "Notes": r.notes,
                        "Error": r.error or "",
                    }
                )
    return path

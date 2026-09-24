# Backlink Checker

Free, open-source backlink checker. No SEMrush, Ahrefs, Moz, or other paid APIs — just local Python that fetches pages and looks for your links.

## What it does

1. Takes **your site** (e.g. `https://mysite.com`) and one or more **target URLs**.
2. Respects each site’s `robots.txt`, waits between requests, and sends a clear User-Agent.
3. Fetches each target page and scans `<a href>` tags for links to your domain (handles http/https, www/non-www, trailing slashes, and subpages).
4. Reports for each page:
   - Backlink found? (Y/N)
   - Exact anchor text
   - dofollow / nofollow (from `rel`)
   - HTTP status (200, 404, redirects, errors)
   - Whether the page might be JavaScript-rendered (if no static match)
5. Optionally re-checks with **Playwright** when static HTML finds nothing (`--js`).
6. Optionally **crawls** a target site and checks every discovered page (`--crawl`).
7. Prints a table and writes a **CSV**.

## Install

```bash
cd backlink-checker
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
```

Optional (only if you use `--js`):

```bash
pip install -r requirements-js.txt
playwright install chromium
```

## Deploy on Render (free)

1. Push this repo to GitHub or GitLab.
2. In [Render](https://render.com): **New → Blueprint** → select the repo.
3. Apply the Blueprint (`render.yaml`). You’ll get a URL like `https://backlink-scout.onrender.com`.

Notes:
- Free instances **sleep after ~15 minutes** idle (first request may be slow).
- Disk is **ephemeral** — CSV files don’t persist across restarts.
- **Deep JS render / Playwright Chromium is not installed** on free; keep that toggle off.

Manual (without Blueprint): Web Service → Python → build `pip install -r requirements.txt` → start `uvicorn backlink_checker.web:app --host 0.0.0.0 --port $PORT`.

## Web UI (recommended)

Scan an **entire website** (all discoverable pages) from a browser:

```bash
# Windows: double-click run.bat, or:
python -m backlink_checker.web
```

Open **http://127.0.0.1:7860**

1. Enter **your website** (e.g. `https://mysite.com`)
2. Enter the **site to crawl** (e.g. `https://directory.example`)
3. Click **Start full-site scan**
4. Watch live progress; filter to pages that contain your link; download CSV

Defaults crawl up to **100 pages** at depth **3**. Raise **Max pages** / **Crawl depth** under Scan options for larger sites (slower, more polite delay recommended).

## CLI usage

```bash
# Single target page
python -m backlink_checker --me https://mysite.com --targets https://example.com/blog/post
# Several targets
python -m backlink_checker --me mysite.com -t https://a.com/page https://b.com/list

# URLs from a file (one per line)
python -m backlink_checker --me https://mysite.com --file urls.txt -o results.csv

# Crawl a directory/site looking for your link anywhere
python -m backlink_checker --me https://mysite.com --crawl https://directory.example --max-depth 2 --max-pages 30

# Static miss → retry with Playwright
python -m backlink_checker --me mysite.com --targets https://spa.example/page --js
```

### Useful flags

| Flag | Meaning |
|------|---------|
| `--me` | Your domain / site URL (required) |
| `--targets` / `-t` | Target page URL(s) |
| `--file` / `-f` | Text file, one URL per line |
| `--crawl URL` | BFS crawl that site for your backlink |
| `--max-depth` | Crawl depth (default `2`) |
| `--max-pages` | Crawl page cap (default `25`) |
| `--js` | Playwright fallback when static HTML has no match |
| `--delay` | Seconds between requests (default `1.5`) |
| `--output` / `-o` | CSV path (default `backlink_results.csv`) |

## Example output

```
| URL                         | Backlink | Anchor text   | Follow   | HTTP status | Notes |
|-----------------------------|----------|---------------|----------|-------------|-------|
| https://example.com/post    | Y        | My cool site  | dofollow | 200         |       |
| https://other.com/list      | N        |               |          | 200         | may be JS-rendered |
```

CSV columns: URL, Backlink, Anchor text, Follow, Matched href, HTTP status, JS suspected, Used Playwright, Notes, Error.

## Project layout

```
backlink_checker/
  cli.py       # argparse CLI
  fetch.py     # requests + rate limit + robots.txt (+ optional Playwright)
  parser.py    # BeautifulSoup backlink scan
  domain.py    # URL / domain matching
  crawl.py     # optional site crawl
  report.py    # table + CSV
```

## Notes / limits

- This checks **known pages** (or pages discovered by crawl). It does **not** discover who links to you across the whole web — that needs a link index (the paid tools you’re avoiding).
- Some sites block scrapers or require login; those show up as errors / non-200 statuses.
- Always be polite: keep `--delay` reasonable and honor robots.txt (already on by default).
- License: use freely; no API keys, no cloud dependency.

"""Local web UI for full-site backlink scanning."""

from __future__ import annotations

import csv
import io
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .crawl import crawl_and_check
from .domain import extract_registrable_host
from .fetch import DEFAULT_DELAY, DEFAULT_USER_AGENT, Fetcher
from .parser import check_page
from .playwright_render import PlaywrightSession
from .report import export_csv, result_to_dict
STATIC_DIR = Path(__file__).resolve().parent / "static"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Backlink Scout", version="1.0.0")

# In-memory job store (single-user local tool)
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


class ScanRequest(BaseModel):
    my_site: str = Field(..., description="Your website URL or domain")
    target_site: str = Field(..., description="Website to crawl and check")
    mode: str = Field(
        "entire_site",
        description="entire_site | url_list",
    )
    urls: str = Field(
        "",
        description="Optional newline-separated URLs when mode=url_list",
    )
    max_depth: int = Field(2, ge=0, le=10)
    max_pages: int = Field(200, ge=1, le=2000)
    delay: float = Field(DEFAULT_DELAY, ge=0.0, le=10.0)
    use_js: bool = False
    use_sitemap: bool = True


def _ensure_url(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    if "://" not in value:
        return "https://" + value
    return value


def _new_job(req: ScanRequest) -> str:
    job_id = uuid.uuid4().hex[:12]
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "request": req.model_dump(),
            "checked": 0,
            "queued": 0,
            "found": 0,
            "max_pages": req.max_pages,
            "message": "Queued…",
            "current_url": "",
            "results": [],
            "csv_path": None,
            "error": None,
            "cancel": False,
            "started_at": time.time(),
            "finished_at": None,
            "js_renders": 0,
            "deep_render": req.use_js,
        }
    return job_id


def _update_job(job_id: str, **fields: Any) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(fields)


def _run_job(job_id: str, req: ScanRequest) -> None:
    try:
        my_host = extract_registrable_host(req.my_site)
        if not my_host:
            _update_job(job_id, status="error", error="Could not parse your site URL", message="Invalid --me URL")
            return

        target = _ensure_url(req.target_site)
        fetcher = Fetcher(user_agent=DEFAULT_USER_AGENT, delay=req.delay)
        results = []

        def on_progress(payload: dict) -> None:
            event = payload.get("event")
            update: dict[str, Any] = {
                "checked": payload.get("checked", 0),
                "queued": payload.get("queued", 0),
                "found": payload.get("found", 0),
                "max_pages": payload.get("max_pages", req.max_pages),
                "message": payload.get("message", ""),
                "current_url": payload.get("url", ""),
                "status": "running",
            }
            if event == "js_render":
                with _jobs_lock:
                    job = _jobs[job_id]
                    job["js_renders"] = int(job.get("js_renders", 0)) + 1
                    job["message"] = payload.get("message", "Deep render (Chromium)…")
                    job["current_url"] = payload.get("url", "")
                    job["status"] = "running"
                return
            if event == "page" and payload.get("result"):
                with _jobs_lock:
                    job = _jobs[job_id]
                    job["results"].append(payload["result"])
                    job.update(update)
            else:
                _update_job(job_id, **update)

        def should_cancel() -> bool:
            with _jobs_lock:
                return bool(_jobs.get(job_id, {}).get("cancel"))

        _update_job(job_id, status="running", message="Starting scan…")

        js_cm = PlaywrightSession(DEFAULT_USER_AGENT) if req.use_js else None
        if js_cm is not None:
            js_cm.__enter__()
            if js_cm.error:
                _update_job(
                    job_id,
                    message=f"Deep render unavailable: {js_cm.error} — continuing static-only",
                )

        try:
            if req.mode == "url_list":
                lines = [
                    line.strip()
                    for line in req.urls.replace(",", "\n").splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
                if not lines and target:
                    lines = [target]
                total = min(len(lines), req.max_pages)
                for i, url in enumerate(lines[: req.max_pages], 1):
                    if should_cancel():
                        break
                    url = _ensure_url(url)
                    _update_job(
                        job_id,
                        message=f"Checking {url}",
                        current_url=url,
                        checked=i - 1,
                        queued=total - i + 1,
                        max_pages=total,
                    )

                    def list_progress(payload: dict, _url=url) -> None:
                        if payload.get("event") == "js_render":
                            with _jobs_lock:
                                job = _jobs[job_id]
                                job["js_renders"] = int(job.get("js_renders", 0)) + 1
                                job["message"] = payload.get("message", "Deep render…")
                                job["current_url"] = payload.get("url", _url)

                    page = check_page(
                        url,
                        my_host,
                        fetcher,
                        use_js=req.use_js and not (js_cm and js_cm.error),
                        on_progress=list_progress if req.use_js else None,
                        js_session=js_cm,
                    )
                    row = result_to_dict(page)
                    results.append(page)
                    with _jobs_lock:
                        job = _jobs[job_id]
                        job["results"].append(row)
                        job["checked"] = i
                        job["found"] = sum(1 for r in job["results"] if r["backlink"])
                        job["queued"] = max(0, total - i)
                        job["message"] = f"Checked {i}/{total}"
            else:
                results = crawl_and_check(
                    target,
                    my_host,
                    fetcher,
                    max_depth=req.max_depth,
                    max_pages=req.max_pages,
                    use_js=req.use_js and not (js_cm and js_cm.error),
                    use_sitemap=req.use_sitemap,
                    js_session=js_cm,
                    on_progress=on_progress,
                    should_cancel=should_cancel,
                )
        finally:
            if js_cm is not None:
                js_cm.close()

        csv_path = OUTPUT_DIR / f"scan_{job_id}.csv"
        export_csv(results, csv_path)
        found = sum(1 for r in results if r.backlink_found)
        status = "cancelled" if should_cancel() else "done"
        _update_job(
            job_id,
            status=status,
            csv_path=str(csv_path),
            found=found,
            checked=len(results),
            message=f"Done — {found}/{len(results)} page(s) have your backlink",
            finished_at=time.time(),
            current_url="",
        )
    except Exception as exc:  # noqa: BLE001
        _update_job(
            job_id,
            status="error",
            error=str(exc),
            message=f"Error: {exc}",
            finished_at=time.time(),
        )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/capabilities")
def capabilities() -> dict:
    from .playwright_render import playwright_status

    return {"playwright": playwright_status()}


@app.post("/api/scan")
def start_scan(req: ScanRequest) -> dict:
    if not req.my_site.strip():
        raise HTTPException(400, "Your website URL is required")
    if req.mode == "entire_site" and not req.target_site.strip():
        raise HTTPException(400, "Target website URL is required")
    if req.mode == "url_list" and not req.urls.strip() and not req.target_site.strip():
        raise HTTPException(400, "Paste at least one target URL")

    job_id = _new_job(req)
    thread = threading.Thread(target=_run_job, args=(job_id, req), daemon=True)
    thread.start()
    return {"job_id": job_id}


@app.get("/api/scan/{job_id}")
def get_scan(job_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        # Return a JSON-safe copy without the cancel flag noise
        return {
            "id": job["id"],
            "status": job["status"],
            "checked": job["checked"],
            "queued": job["queued"],
            "found": job["found"],
            "max_pages": job["max_pages"],
            "message": job["message"],
            "current_url": job["current_url"],
            "results": job["results"],
            "error": job["error"],
            "has_csv": bool(job.get("csv_path")),
            "js_renders": job.get("js_renders", 0),
            "deep_render": job.get("deep_render", False),
            "elapsed_sec": round(
                (job["finished_at"] or time.time()) - job["started_at"], 1
            ),
        }


@app.post("/api/scan/{job_id}/cancel")
def cancel_scan(job_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        job["cancel"] = True
        job["message"] = "Cancelling…"
    return {"ok": True}


@app.get("/api/scan/{job_id}/csv")
def download_csv(job_id: str) -> StreamingResponse:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        path = job.get("csv_path")
        results = list(job["results"])

    if path and Path(path).is_file():
        return FileResponse(
            path,
            media_type="text/csv",
            filename=f"backlink_scan_{job_id}.csv",
        )

    # Fallback: build CSV from in-memory results
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "URL",
            "Backlink",
            "Anchor text",
            "Follow",
            "HTTP status",
            "Notes",
            "Error",
        ],
    )
    writer.writeheader()
    for r in results:
        writer.writerow(
            {
                "URL": r.get("url", ""),
                "Backlink": "Y" if r.get("backlink") else "N",
                "Anchor text": r.get("anchor_text", ""),
                "Follow": r.get("follow", ""),
                "HTTP status": r.get("http_status", ""),
                "Notes": r.get("notes", ""),
                "Error": r.get("error", ""),
            }
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="backlink_scan_{job_id}.csv"'
        },
    )


# Serve CSS/JS next to index
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    """Entry point: python -m backlink_checker.web"""
    import os

    import uvicorn

    port = int(os.environ.get("PORT", "7860"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"\n  Backlink Scout  ->  http://127.0.0.1:{port}\n")
    uvicorn.run(
        "backlink_checker.web:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()

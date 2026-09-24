(() => {
  const form = document.getElementById("scan-form");
  const startBtn = document.getElementById("start-btn");
  const cancelBtn = document.getElementById("cancel-btn");
  const statusEl = document.getElementById("status");
  const resultsEl = document.getElementById("results");
  const resultsBody = document.getElementById("results-body");
  const onlyFound = document.getElementById("only-found");
  const csvBtn = document.getElementById("csv-btn");
  const urlsWrap = document.getElementById("urls-wrap");
  const modeButtons = document.querySelectorAll(".mode");
  const useJs = document.getElementById("use_js");
  const deepCard = document.getElementById("deep-card");
  const deepLabel = document.getElementById("deep-label");
  const pwStatus = document.getElementById("pw-status");
  const jsMeter = document.getElementById("js-meter");
  const themeToggle = document.getElementById("theme-toggle");
  const themeText = document.getElementById("theme-text");

  let mode = "entire_site";
  let jobId = null;
  let pollTimer = null;
  let rows = [];
  let pwReady = false;

  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "light"
      ? "light"
      : "dark";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    themeText.textContent = theme === "light" ? "Light" : "Dark";
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute("content", theme === "light" ? "#f3f7f4" : "#0b110f");
    }
    try {
      localStorage.setItem("bs-theme", theme);
    } catch (_) {}
  }

  applyTheme(currentTheme());
  themeToggle.addEventListener("click", () => {
    applyTheme(currentTheme() === "dark" ? "light" : "dark");
  });

  // Right-edge “How to use” bookmark
  const helpBookmark = document.getElementById("help-bookmark");
  const helpTab = document.getElementById("help-tab");
  const helpClose = document.getElementById("help-close");

  function setHelpOpen(open) {
    helpBookmark.classList.toggle("is-open", open);
    helpTab.setAttribute("aria-expanded", open ? "true" : "false");
  }

  helpTab.addEventListener("click", (e) => {
    e.stopPropagation();
    setHelpOpen(!helpBookmark.classList.contains("is-open"));
  });
  helpClose.addEventListener("click", (e) => {
    e.stopPropagation();
    setHelpOpen(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setHelpOpen(false);
  });
  document.addEventListener("click", (e) => {
    if (!helpBookmark.contains(e.target)) setHelpOpen(false);
  });

  modeButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      mode = btn.dataset.mode;
      modeButtons.forEach((b) => {
        const on = b === btn;
        b.classList.toggle("active", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
      urlsWrap.classList.toggle("is-hidden", mode !== "url_list");
      document.getElementById("target_site").required = mode === "entire_site";
      startBtn.textContent =
        mode === "entire_site" ? "Start full-site scan" : "Check listed URLs";
    });
  });

  function syncDeepUi() {
    const on = useJs.checked;
    deepCard.classList.toggle("is-on", on);
    deepLabel.textContent = on ? "On" : "Off";
  }

  useJs.addEventListener("change", syncDeepUi);
  syncDeepUi();

  async function loadCapabilities() {
    try {
      const res = await fetch("/api/capabilities");
      const data = await res.json();
      const pw = data.playwright || {};
      pwReady = !!pw.ready;
      pwStatus.textContent = pw.message || "";
      pwStatus.classList.remove("warn", "bad");
      if (!pw.available) {
        pwStatus.classList.add("bad");
        deepCard.classList.add("is-unavailable");
        useJs.disabled = true;
        useJs.checked = false;
      } else if (!pw.ready) {
        pwStatus.classList.add("warn");
        deepCard.classList.add("is-unavailable");
        useJs.disabled = true;
        useJs.checked = false;
      } else {
        deepCard.classList.remove("is-unavailable");
        useJs.disabled = false;
      }
      syncDeepUi();
    } catch (_) {
      pwStatus.textContent = "Could not check Chromium status";
      pwStatus.classList.add("warn");
    }
  }

  loadCapabilities();

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    clearInterval(pollTimer);
    rows = [];
    renderRows();

    const payload = {
      my_site: document.getElementById("my_site").value.trim(),
      target_site: document.getElementById("target_site").value.trim(),
      mode,
      urls: document.getElementById("urls").value,
      max_depth: Number(document.getElementById("max_depth").value || 2),
      max_pages: Number(document.getElementById("max_pages").value || 200),
      delay: Number(document.getElementById("delay").value || 1.5),
      use_js: useJs.checked && !useJs.disabled,
      use_sitemap: document.getElementById("use_sitemap").checked,
    };

    startBtn.disabled = true;
    cancelBtn.disabled = false;
    statusEl.hidden = false;
    resultsEl.hidden = false;
    jsMeter.hidden = !payload.use_js;
    document.getElementById("stat-js").textContent = "0";
    setStatus(
      "Starting…",
      payload.use_js
        ? "Crawler + Chromium deep render armed"
        : "Static crawl (enable Deep JS render for SPAs)"
    );
    updateMeters(0, 0, 0, payload.max_pages);
    csvBtn.removeAttribute("href");

    try {
      const res = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || res.statusText);
      }
      const data = await res.json();
      jobId = data.job_id;
      csvBtn.href = `/api/scan/${jobId}/csv`;
      pollTimer = setInterval(poll, 900);
      poll();
    } catch (err) {
      setStatus("Failed", err.message || String(err));
      startBtn.disabled = false;
      cancelBtn.disabled = true;
    }
  });

  cancelBtn.addEventListener("click", async () => {
    if (!jobId) return;
    await fetch(`/api/scan/${jobId}/cancel`, { method: "POST" });
    cancelBtn.disabled = true;
    setStatus("Cancelling…", "Finishing current page, then stopping");
  });

  onlyFound.addEventListener("change", renderRows);

  async function poll() {
    if (!jobId) return;
    try {
      const res = await fetch(`/api/scan/${jobId}`);
      if (!res.ok) return;
      const job = await res.json();
      rows = job.results || [];
      renderRows();
      updateMeters(job.checked, job.found, job.queued, job.max_pages);

      const jsCount = job.js_renders || 0;
      document.getElementById("stat-js").textContent = jsCount;
      if (job.deep_render || jsCount > 0) jsMeter.hidden = false;

      const rendering = (job.message || "").toLowerCase().includes("deep render");
      const labelMap = {
        queued: "Queued",
        running: rendering ? "Deep rendering…" : "Scanning website…",
        done: "Scan complete",
        cancelled: "Scan cancelled",
        error: "Error",
      };
      setStatus(labelMap[job.status] || job.status, job.message || "");
      document.getElementById("current-url").textContent = job.current_url || "";

      if (["done", "cancelled", "error"].includes(job.status)) {
        clearInterval(pollTimer);
        startBtn.disabled = false;
        cancelBtn.disabled = true;
        document.getElementById("current-url").textContent = job.error
          ? job.error
          : `Elapsed ${job.elapsed_sec}s` +
            (jsCount ? ` · ${jsCount} Chromium deep render(s)` : "");
      }
    } catch (_) {
      /* keep polling */
    }
  }

  function setStatus(label, msg) {
    document.getElementById("status-label").textContent = label;
    document.getElementById("status-msg").textContent = msg || "";
  }

  function updateMeters(checked, found, queued, maxPages) {
    document.getElementById("stat-checked").textContent = checked;
    document.getElementById("stat-found").textContent = found;
    document.getElementById("stat-queued").textContent = queued;
    const pct = maxPages ? Math.min(100, Math.round((checked / maxPages) * 100)) : 0;
    document.getElementById("progress-bar").style.width = `${pct}%`;
  }

  function renderRows() {
    const filterOn = onlyFound.checked;
    const visible = filterOn ? rows.filter((r) => r.backlink) : rows;
    resultsBody.innerHTML = visible
      .map((r) => {
        const badge = r.backlink
          ? `<span class="badge yes">Y</span>`
          : `<span class="badge no">N</span>`;
        const jsBadge = r.used_playwright
          ? `<span class="badge js">JS</span>`
          : r.js_suspected
            ? `<span class="badge spa">SPA?</span>`
            : "";
        const follow = r.follow
          ? `<span class="badge follow-${r.follow}">${escapeHtml(r.follow)}</span>`
          : "";
        return `<tr class="${r.backlink ? "row-found" : ""}">
          <td><a href="${escapeAttr(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.url)}</a></td>
          <td>${badge}${jsBadge}</td>
          <td>${escapeHtml(r.anchor_text || "")}</td>
          <td>${follow}</td>
          <td>${escapeHtml(r.http_status || "")}</td>
          <td class="notes">${escapeHtml(r.notes || r.error || "")}</td>
        </tr>`;
      })
      .join("");
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }
})();

/* Fluffless — front-end logic. Talks to the local JSON API, renders the
   editorial UI, and never re-renders wholesale where a patch will do. */

(() => {
  "use strict";

  // ---------- theme ----------
  const THEME_COLORS = { dark: "#0f0d0b", light: "#efe6d8" };
  function applyTheme(mode) {
    if (mode === "light") document.documentElement.setAttribute("data-theme", "light");
    else document.documentElement.removeAttribute("data-theme");
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", THEME_COLORS[mode] || THEME_COLORS.dark);
    const tg = document.getElementById("themeToggle");
    if (tg) tg.setAttribute("aria-checked", mode === "light" ? "true" : "false");
  }
  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
  }
  (function initTheme() {
    const stored = localStorage.getItem("theme");
    const prefersLight = matchMedia("(prefers-color-scheme: light)").matches;
    applyTheme(stored || (prefersLight ? "light" : "dark"));
  })();
  document.getElementById("themeToggle").addEventListener("click", () => {
    const next = currentTheme() === "light" ? "dark" : "light";
    localStorage.setItem("theme", next);
    applyTheme(next);
  });

  // ---------- icons ----------
  const icon = (paths, size = 16, stroke = 2) =>
    `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const ICON_AUDIO = '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>';
  const ICON_VIDEO = '<path d="m22 8-6 4 6 4V8z"/><rect x="2" y="6" width="14" height="12" rx="2"/>';
  const ICON_EMPTY = '<path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2z"/>';
  // Lucide glyphs (MIT) — one source of truth so every icon shares size & stroke.
  const ICON_SCAN     = '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M7 12h10"/>';
  const ICON_TRASH    = '<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>';
  const ICON_PLAY     = '<polygon points="6 3 20 12 6 21 6 3"/>';
  const ICON_PENCIL   = '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/>';
  const ICON_CHECK    = '<path d="M20 6 9 17l-5-5"/>';
  const ICON_X        = '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>';

  // ---------- api ----------
  async function api(path, opts) {
    const res = await fetch(path, opts);
    const ct = res.headers.get("Content-Type") || "";
    if (!ct.includes("application/json")) return res;
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }
  const post = (path, body) =>
    api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

  // ---------- toast ----------
  let toastTimer = null;
  function toast(msg, variant) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.className = "toast" + (variant ? " " + variant : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), 3500);
  }

  // ---------- state ----------
  const state = {
    library: null,
    folders: [],
    folder: null,        // active folder object
    filesCollapsed: false,
    processedNames: new Set(),
    patterns: [],
  };

  // Review lifecycle, mirrored from the server: a detected segment is pending
  // until the user decides; confirmed = "this is an ad, remove it"; dismissed =
  // "not an ad, leave it". Pending sorts first so what needs attention leads.
  const STATUS_VERB = { pending: "Needs review", confirmed: "Confirmed ad", dismissed: "Not an ad" };
  const STATUS_ORDER = { pending: 0, confirmed: 1, dismissed: 2 };

  const $ = (id) => document.getElementById(id);
  const fmtDur = (s) => {
    s = Math.round(s || 0); const m = Math.floor(s / 60); const sec = s % 60;
    return `${m}:${String(sec).padStart(2, "0")}`;
  };
  const fmtSize = (b) => {
    if (!b) return "—";
    const u = ["B", "KB", "MB", "GB"]; let i = 0; let n = b;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
  };

  // ---------- boot ----------
  async function boot() {
    try {
      const st = await api("/api/status");
      renderEngines(st.tools);
      if (st.library) {
        state.library = st.library;
        state.folders = st.folders;
        $("libraryInput").value = st.library;
        renderFolders();
      }
    } catch (e) { toast(e.message, "error"); }
  }

  function renderEngines(tools) {
    const row = $("engineRow");
    const chip = (name, ok) =>
      `<span class="status-chip ${ok ? "ok" : "bad"}">${name} ${ok ? "ready" : "missing"}</span>`;
    row.innerHTML = chip("ffmpeg", tools.ffmpeg) + chip("fpcalc", tools.fpcalc);
  }

  // ---------- library ----------
  $("openBtn").addEventListener("click", openLibrary);
  $("libraryInput").addEventListener("keydown", (e) => { if (e.key === "Enter") openLibrary(); });
  async function openLibrary() {
    const path = $("libraryInput").value.trim();
    if (!path) { toast("Enter a folder path", "error"); return; }
    try {
      const data = await post("/api/library", { path });
      state.library = data.library;
      state.folders = data.folders;
      state.folder = null;
      renderFolders();
      toast(`Opened ${data.folders.length} media folder(s)`, "notice");
    } catch (e) { toast(e.message, "error"); }
  }

  // ---------- folders grid ----------
  function renderFolders() {
    showWorkspace(false);
    $("foldersSection").classList.remove("hidden");
    $("footer").classList.remove("hidden");
    const grid = $("foldersGrid");
    if (!state.folders.length) {
      grid.innerHTML = emptyState("No media folders found", "Drop show folders into this library and reopen it.");
      refreshProcessed();
      return;
    }
    grid.innerHTML = "";
    state.folders.forEach((f) => {
      const card = document.createElement("button");
      card.className = "folder-card";
      const logo = f.kind === "video" ? ICON_VIDEO : ICON_AUDIO;
      card.innerHTML = `
        <span class="folder-logo">${icon(logo, 22)}<span class="kind">${f.kind}</span></span>
        <span class="folder-name">${escapeHtml(f.name)}</span>
        <span class="folder-meta">${f.count} file${f.count === 1 ? "" : "s"}</span>`;
      card.addEventListener("click", () => openFolder(f.name));
      grid.appendChild(card);
    });
    refreshProcessed();
  }

  // ---------- folder workspace ----------
  async function openFolder(name) {
    const folder = state.folders.find((f) => f.name === name);
    if (!folder) return;
    state.folder = folder;
    state.filesCollapsed = folder.files.length > COLLAPSE_THRESHOLD;
    $("foldersSection").classList.add("hidden");
    showWorkspace(true);
    $("wsTitle").textContent = folder.name;
    $("wsMarker").textContent = folder.kind === "video" ? "Video." : "Audio.";
    renderFileList();
    await Promise.all([loadPatterns(), refreshProcessed(), checkNewFiles(name)]);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const COLLAPSE_THRESHOLD = 8;
  function applyFilesCollapsed() {
    $("filesBlock").classList.toggle("collapsed", !!state.filesCollapsed);
    $("filesToggle").setAttribute("aria-expanded", state.filesCollapsed ? "false" : "true");
  }

  function showWorkspace(on) {
    $("workspaceSection").classList.toggle("hidden", !on);
  }

  $("backBtn").addEventListener("click", () => { state.folder = null; renderFolders(); window.scrollTo({ top: 0 }); });

  // file-list toggle (read-only list; scanning always covers the whole folder)
  $("filesToggle").addEventListener("click", () => {
    state.filesCollapsed = !state.filesCollapsed;
    applyFilesCollapsed();
  });

  function renderFileList() {
    const list = $("fileList");
    $("filesCount").textContent = String(state.folder.files.length);
    list.innerHTML = "";
    state.folder.files.forEach((f) => {
      const done = state.processedNames && state.processedNames.has(f.name);
      const row = document.createElement("div");
      row.className = "file-row" + (done ? " processed" : "");
      row.innerHTML = `
        <span class="fname">${escapeHtml(f.name)}</span>
        <span class="fmeta">${f.kind} · ${fmtDur(f.duration)} · ${fmtSize(f.size)}</span>`;
      list.appendChild(row);
    });
    applyFilesCollapsed();
  }

  // ---------- new-files check ----------
  async function checkNewFiles(folderName) {
    try {
      const res = await api(`/api/scan/new?folder=${encodeURIComponent(folderName)}`);
      const btn = $("processNewBtn");
      const label = btn.querySelector(".btn-label");
      if (res.new_count > 0) {
        label.textContent = `Process ${res.new_count} new file${res.new_count === 1 ? "" : "s"}`;
        btn.classList.remove("hidden");
        $("scanHint").innerHTML = `<em>New files.</em> ${res.new_count} file${res.new_count === 1 ? "" : "s"} added since the last scan. Process just these to apply confirmed patterns — or run a full scan to also discover new segments.`;
      } else {
        btn.classList.add("hidden");
        $("scanHint").innerHTML = `<em>Scan.</em> Fingerprints every file and finds the segments that recur across the folder.`;
      }
    } catch (_) { /* non-fatal */ }
  }

  $("processNewBtn").addEventListener("click", () => runScan({ incremental: true }));

  // ---------- scan ----------
  $("scanBtn").addEventListener("click", () => runScan({}));
  let scanSource = null;

  async function runScan({ incremental = false } = {}) {
    const folder = state.folder;
    if (!folder) return;
    $("scanBtn").disabled = true;
    $("processNewBtn").disabled = true;
    startScanUI();
    const minLen = Math.max(1, parseFloat($("minLen").value) || 25);
    try {
      const res = await post("/api/scan", { folder: folder.name, files: null, min_seconds: minLen, incremental });
      setScanFile(`${res.total_files} file${res.total_files === 1 ? "" : "s"} queued`);
      openScanStream();
    } catch (e) {
      toast(e.message, "error");
      endScanUI("error", e.message);
      $("scanBtn").disabled = false;
      $("processNewBtn").disabled = false;
    }
  }

  function openScanStream() {
    if (scanSource) { scanSource.close(); scanSource = null; }
    state.scanDone = false;
    scanSource = new EventSource("/api/scan/stream");
    scanSource.onmessage = (e) => {
      let ev;
      try { ev = JSON.parse(e.data); } catch { return; }
      handleScanEvent(ev);
    };
    scanSource.onerror = () => {
      // A normal close (after the 'end' sentinel) is fine; only react if the
      // stream dropped mid-scan.
      if (state.scanDone) return;
      if (scanSource) { scanSource.close(); scanSource = null; }
      toast("Progress stream lost — the scan may still be running", "error");
      $("scanBtn").disabled = false;
      loadPatterns();
    };
  }

  const STAGE_WORDS = {
    fingerprint: "Reading", detect: "Detecting", detect_progress: "Detecting",
    matched: "Matching", found: "Detecting", preview: "Previews",
    done: "Finalising", result: "Complete", error: "Reading",
  };

  function handleScanEvent(ev) {
    if (ev.stage === "end") { closeScan(); return; }
    if (ev.stage === "fatal") {
      toast(ev.message || "Scan failed", "error");
      endScanUI("error", ev.message || "Scan failed");
      return;
    }
    if (ev.stage === "warn") {
      if (ev.message) console.warn(ev.message);   // surfaced as a summary at the end
      return;
    }
    if (typeof ev.percent === "number") setScanPercent(ev.percent);
    if (STAGE_WORDS[ev.stage]) $("scanStage").textContent = STAGE_WORDS[ev.stage];
    if (ev.detail || ev.message) setScanFile(ev.detail || ev.message);
    if (ev.stage === "fingerprint" || ev.stage === "detect_progress") setScanEta(ev.eta_seconds);

    if (ev.stage === “result”) {
      state.patterns = ev.patterns || [];
      renderPatterns();
      endScanUI(“complete”);
      const found = (ev.new_patterns || []).length;
      const matched = (ev.matched_patterns || []).length;
      const n = ev.files_scanned || 0;
      const failed = ev.previews_failed || 0;
      if (failed) {
        toast(`Scanned ${n} file(s) · ${failed} preview${failed === 1 ? “” : “s”} couldn't be built — use “Generate preview” to retry`, “error”);
      } else if (found === 0 && matched > 0) {
        // Incremental (or full scan where confirmed patterns were the only hits)
        toast(`Processed ${n} file${n === 1 ? “” : “s”} · matched confirmed segments in ${matched} pattern${matched === 1 ? “” : “s”}`, “notice”);
      } else if (found === 0 && matched === 0) {
        toast(`Processed ${n} file${n === 1 ? “” : “s”} · no confirmed segments found in these files`, “notice”);
      } else {
        toast(`Scanned ${n} file${n === 1 ? “” : “s”} · ${found} new segment${found === 1 ? “” : “s”} · ${matched} matched`, “notice”);
      }
    }
  }

  function closeScan() {
    state.scanDone = true;
    if (scanSource) { scanSource.close(); scanSource = null; }
    $("scanBtn").disabled = false;
    $("processNewBtn").disabled = false;
    if (state.folder) checkNewFiles(state.folder.name);
  }

  // --- scan progress UI helpers ---
  function startScanUI() {
    const box = $("scanStatus");
    box.classList.remove("hidden", "complete", "error");
    state.scanPct = 0;
    $("scanStage").textContent = "Starting";
    $("scanPct").textContent = "0%";
    $("scanBar").querySelector(".bar").style.width = "0%";
    $("scanFile").textContent = "";
    $("scanEta").textContent = "";
  }
  function setScanPercent(p) {
    p = Math.max(state.scanPct || 0, Math.min(100, p)); // keep the bar monotonic
    state.scanPct = p;
    $("scanBar").querySelector(".bar").style.width = p + "%";
    $("scanPct").textContent = Math.round(p) + "%";
  }
  function setScanFile(t) { $("scanFile").textContent = t || ""; }
  function setScanEta(s) { $("scanEta").textContent = fmtEta(s); }
  function endScanUI(kind, msg) {
    closeScan();
    const box = $("scanStatus");
    if (kind === "complete") {
      box.classList.add("complete");
      setScanPercent(100);
      $("scanStage").textContent = "Complete";
      $("scanEta").textContent = "";
    } else if (kind === "error") {
      box.classList.add("error");
      $("scanStage").textContent = "Failed";
      if (msg) setScanFile(msg);
      $("scanEta").textContent = "";
    } else {
      box.classList.add("hidden");
    }
  }
  function fmtEta(s) {
    if (s == null) return "estimating…";
    if (s <= 1) return "almost done";
    if (s < 60) return `~${Math.round(s)}s left`;
    const m = Math.floor(s / 60), sec = Math.round(s % 60);
    return sec ? `~${m}m ${sec}s left` : `~${m}m left`;
  }

  async function loadPatterns() {
    try {
      const res = await api(`/api/patterns?folder=${encodeURIComponent(state.folder.name)}`);
      state.patterns = res.patterns;
      renderPatterns();
    } catch (e) { toast(e.message, "error"); }
  }

  // ---------- patterns ----------
  function renderPatterns() {
    const wrap = $("patternsList");
    const chip = $("dupCount");
    if (!state.patterns.length) {
      chip.classList.add("hidden");
      wrap.innerHTML = emptyState("No duplicates catalogued yet",
        "Run a scan to find segments that recur across these files.");
      return;
    }
    chip.textContent = String(state.patterns.length);
    chip.classList.remove("hidden");
    wrap.innerHTML = "";
    const ordered = [...state.patterns].sort((a, b) =>
      (STATUS_ORDER[a.status] ?? 0) - (STATUS_ORDER[b.status] ?? 0) || a.id - b.id);
    ordered.forEach((p) => wrap.appendChild(renderPattern(p)));
    updateRemoveSummary();
  }

  function renderPattern(p) {
    const el = document.createElement("div");
    el.className = "pattern " + (p.status || "pending");
    el.dataset.id = p.id;

    const clips = p.clips.map((c) => renderClip(c)).join("");

    el.innerHTML = `
      <div class="pattern-head">
        <div class="pattern-title">
          <span class="status-verb">${STATUS_VERB[p.status] || STATUS_VERB.pending}</span>
          <span class="pattern-chips">
            <span class="chip-mono">${fmtDur(p.duration)}</span>
            <span class="chip-mono">${p.clips.length} occurrence${p.clips.length === 1 ? "" : "s"}</span>
            ${p.pinned ? `<span class="chip-mono custom" title="This segment's fingerprint was cropped by hand">cropped</span>` : ""}
          </span>
        </div>
        <div class="pattern-controls">
          <div class="review-controls">
            <button class="review-btn ad ${p.status === "confirmed" ? "active" : ""}" data-decision="ad"
              title="Confirm this is an ad — cut it from every file">${icon(ICON_CHECK, 14)}<span>Mark as ad</span></button>
            <button class="review-btn no ${p.status === "dismissed" ? "active" : ""}" data-decision="not_ad"
              title="Not an ad — set it aside">${icon(ICON_X, 14)}<span>Not an ad</span></button>
          </div>
          <button class="icon-btn danger pattern-del" title="Delete segment" aria-label="Delete segment">${icon(ICON_TRASH, 15)}</button>
        </div>
      </div>
      <div class="clip-list">${clips}</div>`;

    el.querySelectorAll(".review-controls .review-btn").forEach((b) => {
      b.addEventListener("click", () => reviewPattern(p, b.dataset.decision));
    });
    el.querySelector(".pattern-del").addEventListener("click", () => deletePattern(p.id));
    el.querySelectorAll(".preview-btn").forEach((b) => {
      const clip = p.clips.find((c) => c.id === Number(b.dataset.clip));
      b.addEventListener("click", () => playClip(b, clip));
    });
    el.querySelectorAll(".adjust-btn").forEach((b) => {
      const clip = p.clips.find((c) => c.id === Number(b.dataset.clip));
      b.addEventListener("click", () => toggleClipEditor(b, clip, p));
    });
    return el;
  }

  function renderClip(c) {
    const range = `${fmtDur(c.start)}–${fmtDur(c.end)}`;
    return `
      <div class="clip" data-clip="${c.id}">
        <span class="clip-info"><span class="cfile">${escapeHtml(c.file_name)}</span> · <span class="crange">${range}</span></span>
        <div class="clip-actions">
          <button class="preview-btn" data-clip="${c.id}">${icon(ICON_PLAY, 12)}<span class="pv-label">Preview</span></button>
          <button class="icon-btn adjust-btn" data-clip="${c.id}" title="Adjust this clip's boundaries" aria-label="Adjust this clip's boundaries">${icon(ICON_PENCIL, 14)}</button>
        </div>
        <div class="clip-media" data-media="${c.id}"></div>
      </div>`;
  }

  function toggleClipEditor(btn, c, p) {
    const clipEl = btn.closest(".clip");
    const slot = clipEl.querySelector(".clip-media");
    if (slot.dataset.editor) {
      slot.innerHTML = ""; delete slot.dataset.editor; delete slot.dataset.loaded;
      btn.classList.remove("active");
      return;
    }
    slot.innerHTML = `
      <div class="clip-edit">
        <div class="edit-fields">
          <label>start <input type="number" class="num-input edit-start" step="0.1" min="0" value="${c.start}"/>s</label>
          <label>end <input type="number" class="num-input edit-end" step="0.1" min="0" value="${c.end}"/>s</label>
          <button class="btn-ghost save-bounds">Save &amp; preview</button>
          <button class="btn-ghost clip-reset-btn">Reset</button>
        </div>
        <div class="edit-actions">
          <button class="btn-primary clip-relocate-btn" title="Find this exact segment across every scanned file: bring matching episodes into this group at the same length, and split clips that don't match into their own group">${icon(ICON_SCAN, 14)}<span class="btn-label">Match across all</span></button>
        </div>
        <p class="edit-hint"><em>Match across all.</em> Crop this clip to the exact ad, then
          match it everywhere — every episode that contains it joins this group at the same
          length, and clips that don't match move to their own group.</p>
        <div class="edit-player"></div>
      </div>`;
    slot.dataset.editor = "1";
    btn.classList.add("active");
    slot.querySelector(".save-bounds").addEventListener("click", () => saveClipBounds(clipEl, c, slot));
    slot.querySelector(".clip-reset-btn").addEventListener("click", () => resetClip(c, slot, clipEl));
    slot.querySelector(".clip-relocate-btn").addEventListener("click", () => relocateFromClip(c, slot));
  }

  async function saveClipBounds(clipEl, c, slot) {
    const start = parseFloat(slot.querySelector(".edit-start").value);
    const end = parseFloat(slot.querySelector(".edit-end").value);
    if (!(end - start >= 0.2)) { toast("End must be at least 0.2s after start", "error"); return; }
    const saveBtn = slot.querySelector(".save-bounds");
    saveBtn.disabled = true; saveBtn.textContent = "Saving…";
    try {
      const res = await post("/api/clip/adjust", { clip_id: c.id, start, end });
      c.start = res.start; c.end = res.end; c.has_preview = res.has_preview;
      updateClipInState(c.id, res.start, res.end, res.has_preview);
      clipEl.querySelector(".crange").textContent = `${fmtDur(c.start)}–${fmtDur(c.end)}`;
      if (res.has_preview) {
        const tag = state.folder && state.folder.kind === "video" ? "video" : "audio";
        slot.querySelector(".edit-player").innerHTML =
          `<${tag} controls autoplay src="/api/preview/${c.id}?t=${Date.now()}"></${tag}>`;
        toast("Saved — playing the refined clip", "notice");
      } else {
        toast("Saved" + (res.preview_error ? ` (preview failed: ${res.preview_error})` : ""),
          res.preview_error ? "error" : "notice");
      }
    } catch (e) {
      toast(e.message, "error");
    } finally {
      saveBtn.disabled = false; saveBtn.textContent = "Save & preview";
    }
  }

  function updateClipInState(clipId, start, end, hasPreview) {
    state.patterns.forEach((p) => p.clips.forEach((c) => {
      if (c.id === clipId) { c.start = start; c.end = end; if (hasPreview !== undefined) c.has_preview = hasPreview; }
    }));
  }

  async function resetClip(c, slot, clipEl) {
    const resetBtn = slot.querySelector(".clip-reset-btn");
    resetBtn.disabled = true; resetBtn.textContent = "Resetting…";
    try {
      const res = await post("/api/clip/reset", { clip_id: c.id });
      c.start = res.start; c.end = res.end; c.has_preview = false;
      updateClipInState(c.id, res.start, res.end, false);
      clipEl.querySelector(".crange").textContent = `${fmtDur(res.start)}–${fmtDur(res.end)}`;
      slot.querySelector(".edit-start").value = res.start;
      slot.querySelector(".edit-end").value = res.end;
      slot.querySelector(".edit-player").innerHTML = "";
      delete slot.dataset.loaded;
      toast("Clip reset to detected bounds", "notice");
    } catch (e) {
      toast(e.message, "error");
    } finally {
      resetBtn.disabled = false; resetBtn.textContent = "Reset to detected";
    }
  }

  // "I found the ad — find it everywhere." Crops the clip (if edited), pins it
  // as the group's fingerprint, then re-derives the whole group: matches snap to
  // the same length, new matching episodes are pulled in, and non-matches split
  // off into their own group.
  async function relocateFromClip(c, slot) {
    const start = parseFloat(slot.querySelector(".edit-start").value);
    const end = parseFloat(slot.querySelector(".edit-end").value);
    if (!(end - start >= 0.2)) { toast("End must be at least 0.2s after start", "error"); return; }
    const btn = slot.querySelector(".clip-relocate-btn");
    const label = btn.querySelector(".btn-label");
    btn.disabled = true; if (label) label.textContent = "Matching…";
    try {
      const res = await post("/api/clip/relocate", { clip_id: c.id, start, end });
      state.patterns = res.patterns || state.patterns;
      renderPatterns();
      const parts = [`${res.snapped + res.added} clip(s) at ${fmtDur(res.duration)}`];
      if (res.added) parts.push(`${res.added} new`);
      if (res.moved_out) parts.push(`${res.moved_out} moved to a new group`);
      if (res.deduped) parts.push(`${res.deduped} nested duplicate(s) cleaned up`);
      toast(`Matched across files — ${parts.join(", ")}`, "notice");
    } catch (e) {
      toast(e.message, "error");
      btn.disabled = false; if (label) label.textContent = "Match across all";
    }
  }

  async function playClip(btn, c) {
    const slot = btn.closest(".clip").querySelector(".clip-media");
    const setLabel = (ico, txt) => { btn.innerHTML = `${icon(ico, 12)}<span class="pv-label">${txt}</span>`; };
    if (slot.dataset.loaded) {
      slot.innerHTML = ""; delete slot.dataset.loaded; setLabel(ICON_PLAY, "Preview"); return;
    }
    if (c && !c.has_preview) {                 // build it on first play — no separate button
      btn.disabled = true; btn.innerHTML = `<span class="pv-spin"></span><span class="pv-label">Generating</span>`;
      try {
        await post("/api/preview", { clip_id: c.id });
        c.has_preview = true; updateClipInState(c.id, c.start, c.end, true);
      } catch (e) {
        toast(e.message, "error"); btn.disabled = false; setLabel(ICON_PLAY, "Preview"); return;
      }
      btn.disabled = false;
    }
    const tag = state.folder && state.folder.kind === "video" ? "video" : "audio";
    slot.innerHTML = `<${tag} controls autoplay src="/api/preview/${c.id}?t=${Date.now()}"></${tag}>`;
    slot.dataset.loaded = "1";
    setLabel(ICON_X, "Hide");
  }

  // Record an Ad / Not-an-ad verdict. Re-clicking the current verdict toggles
  // back to needs-review, so a mistaken tap is one click to undo. Confirming an
  // ad re-parses every file for all its occurrences (server-side).
  async function reviewPattern(p, decision) {
    let target = decision;
    if (decision === "ad" && p.status === "confirmed") target = "pending";
    else if (decision === "not_ad" && p.status === "dismissed") target = "pending";
    try {
      const res = await post("/api/pattern/review", { pattern_id: p.id, decision: target });
      state.patterns = res.patterns || state.patterns;
      renderPatterns();
      if (res.status === "confirmed") {
        const parts = [];
        if (res.applied_to) parts.push(`found ${res.applied_to} occurrence${res.applied_to === 1 ? "" : "s"} across your files`);
        if (res.absorbed) parts.push(`dismissed ${res.absorbed} overlapping card${res.absorbed === 1 ? "" : "s"}`);
        if (res.deduped) parts.push(`cleaned up ${res.deduped} nested duplicate${res.deduped === 1 ? "" : "s"}`);
        toast(parts.length ? `Confirmed — ${parts.join(", ")}` : "Confirmed as an ad — will be removed", "notice");
      } else if (res.status === "dismissed") {
        toast("Set aside — won't be removed", "notice");
      } else {
        toast("Back to needs-review", "notice");
      }
    } catch (e) { toast(e.message, "error"); }
  }

  async function deletePattern(id) {
    try {
      await api(`/api/pattern/${id}`, { method: "DELETE" });
      state.patterns = state.patterns.filter((p) => p.id !== id);
      renderPatterns();
      toast("Segment removed", "notice");
    } catch (e) { toast(e.message, "error"); }
  }

  // ---------- remove the fluff ----------
  // Removal acts on everything confirmed as an ad — no per-category picking.
  function updateRemoveSummary() {
    const confirmed = state.patterns.filter((p) => p.status === "confirmed");
    const clips = confirmed.reduce((n, p) => n + p.clips.length, 0);
    $("removeSummary").textContent = clips
      ? `${clips} occurrence${clips === 1 ? "" : "s"} from ${confirmed.length} confirmed segment${confirmed.length === 1 ? "" : "s"} will be cut`
      : "Mark a segment as an ad above to remove it";
    $("removeBtn").disabled = !clips;
  }

  let removeConfirm = false;
  let removeConfirmTimer = null;
  const setRemoveLabel = (txt) => {
    const lbl = $("removeBtn").querySelector(".btn-label");
    if (lbl) lbl.textContent = txt; else $("removeBtn").textContent = txt;
  };
  $("removeBtn").addEventListener("click", () => {
    const btn = $("removeBtn");
    if (!removeConfirm) {
      removeConfirm = true;
      btn.classList.add("confirm");
      setRemoveLabel("Tap again to confirm");
      removeConfirmTimer = setTimeout(() => {
        removeConfirm = false; btn.classList.remove("confirm"); setRemoveLabel("Remove the fluff");
      }, 3000);
      return;
    }
    clearTimeout(removeConfirmTimer);
    removeConfirm = false; btn.classList.remove("confirm"); setRemoveLabel("Remove the fluff");
    runRemove();
  });

  let removeSource = null;
  async function runRemove() {
    if (!state.patterns.some((p) => p.status === "confirmed")) return;
    const btn = $("removeBtn");
    btn.disabled = true;
    $("removeResults").innerHTML = "";
    startRemoveUI();
    try {
      await post("/api/remove", { folder: state.folder.name });
      openRemoveStream();
    } catch (e) {
      toast(e.message, "error");
      endRemoveUI("error", e.message);
      btn.disabled = false;
    }
  }

  function openRemoveStream() {
    if (removeSource) { removeSource.close(); removeSource = null; }
    state.removeDone = false;
    removeSource = new EventSource("/api/remove/stream");
    removeSource.onmessage = (e) => {
      let ev; try { ev = JSON.parse(e.data); } catch { return; }
      handleRemoveEvent(ev);
    };
    removeSource.onerror = () => {
      if (state.removeDone) return;
      if (removeSource) { removeSource.close(); removeSource = null; }
      toast("Progress stream lost — removal may still be running", "error");
      $("removeBtn").disabled = false;
      refreshProcessed();
    };
  }

  function handleRemoveEvent(ev) {
    if (ev.stage === "end") {
      state.removeDone = true;
      if (removeSource) { removeSource.close(); removeSource = null; }
      $("removeBtn").disabled = false;
      return;
    }
    if (ev.stage === "fatal") {
      toast(ev.message || "Removal failed", "error");
      endRemoveUI("error", ev.message);
      return;
    }
    if (typeof ev.percent === "number") {
      $("removeBar").querySelector(".bar").style.width = Math.min(100, ev.percent) + "%";
      $("removePct").textContent = Math.round(ev.percent) + "%";
    }
    if (ev.message) $("removeFile").textContent = ev.message;
    if (ev.stage === "file" && ev.result) appendRemoveResult(ev.result);
    if (ev.stage === "result") {
      endRemoveUI("complete");
      const saved = (ev.results || []).reduce((s, r) => s + (r.saved_sec || 0), 0);
      const failed = (ev.results || []).filter((r) => r.error).length;
      toast(failed
        ? `${ev.message} · ${failed} failed · saved ${fmtDur(saved)}`
        : `${ev.message} · saved ${fmtDur(saved)} → ${ev.out_dir}`,
        failed ? "error" : "notice");
      refreshProcessed();
    }
  }

  function startRemoveUI() {
    const box = $("removeStatus");
    box.classList.remove("hidden", "complete", "error");
    $("removeStage").textContent = "Trimming";
    $("removePct").textContent = "0%";
    $("removeBar").querySelector(".bar").style.width = "0%";
    $("removeFile").textContent = "";
  }
  function endRemoveUI(kind, msg) {
    const box = $("removeStatus");
    if (kind === "complete") {
      box.classList.add("complete");
      $("removeStage").textContent = "Complete";
      $("removePct").textContent = "100%";
      $("removeBar").querySelector(".bar").style.width = "100%";
    } else if (kind === "error") {
      box.classList.add("error");
      $("removeStage").textContent = "Failed";
      if (msg) $("removeFile").textContent = msg;
    }
    $("removeBtn").disabled = false;
  }
  function appendRemoveResult(r) {
    const wrap = $("removeResults");
    const div = document.createElement("div");
    div.className = "remove-result";
    div.innerHTML = r.error
      ? `<span class="err">${icon(ICON_X, 12)} ${escapeHtml(r.file)}</span><span>${escapeHtml(r.error)}</span>`
      : `<span class="ok">${icon(ICON_CHECK, 12)} ${escapeHtml(r.file)}</span><span>saved ${fmtDur(r.saved_sec)} · ${r.segments} cut → ${escapeHtml(r.output)}</span>`;
    wrap.appendChild(div);
  }

  // ---------- export & processed ----------
  $("exportJson").addEventListener("click", () => download("/api/export?format=json"));
  $("exportMd").addEventListener("click", () => download("/api/export?format=md"));
  function download(url) {
    if (!state.library) { toast("Open a library first", "error"); return; }
    const a = document.createElement("a");
    a.href = url; a.download = ""; document.body.appendChild(a); a.click(); a.remove();
  }

  async function refreshProcessed() {
    try {
      const res = await api("/api/processed");
      state.processedNames = new Set(res.processed.map((r) => r.file_name));
      if (state.folder) renderFileList();   // reflect "trimmed" tags in the list
      const wrap = $("processedList");
      if (!res.processed.length) {
        wrap.innerHTML = `<p class="hint"><em>History.</em> Files you trim will be logged here and preserved across runs.</p>`;
        return;
      }
      wrap.innerHTML = res.processed.map((r) =>
        `<div class="proc-row"><span class="pfile">${escapeHtml(r.file_name)}</span>
         <span class="saved">saved ${fmtDur(r.saved_sec)}</span>
         <span>· ${r.segments} cut</span></div>`).join("");
    } catch (_) { /* library not open yet */ }
  }

  // ---------- helpers ----------
  function emptyState(h, p) {
    return `<div class="empty-state"><div class="ic">${icon(ICON_EMPTY, 40, 1.3)}</div>
      <h3>${escapeHtml(h)}</h3><p>${escapeHtml(p)}</p></div>`;
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  boot();
})();

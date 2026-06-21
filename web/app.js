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
    labels: ["Ad", "Intro", "Outro", "Other"],
    folders: [],
    folder: null,        // active folder object
    scope: "all",
    selectedFiles: new Set(),
    filesCollapsed: false,
    processedNames: new Set(),
    patterns: [],
    removeLabels: new Set(["Ad"]),
  };

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
      state.labels = st.labels || state.labels;
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
    state.scope = "all";
    state.selectedFiles = new Set(folder.files.map((f) => f.path));
    state.filesCollapsed = folder.files.length > COLLAPSE_THRESHOLD;
    $("foldersSection").classList.add("hidden");
    showWorkspace(true);
    $("wsTitle").textContent = folder.name;
    $("wsMarker").textContent = folder.kind === "video" ? "Video." : "Audio.";
    document.querySelectorAll("#scopeSeg button").forEach((b) =>
      b.classList.toggle("active", b.dataset.scope === "all"));
    renderFileList();
    renderRemoveLabels();
    await Promise.all([loadPatterns(), refreshProcessed()]);
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

  // scope toggle
  document.querySelectorAll("#scopeSeg button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.scope = btn.dataset.scope;
      document.querySelectorAll("#scopeSeg button").forEach((b) =>
        b.classList.toggle("active", b === btn));
      if (state.scope === "partial") state.filesCollapsed = false; // reveal to pick
      renderFileList();
    });
  });

  // file-list controls
  $("filesToggle").addEventListener("click", () => {
    state.filesCollapsed = !state.filesCollapsed;
    applyFilesCollapsed();
  });
  $("selectAll").addEventListener("click", () => {
    state.selectedFiles = new Set(state.folder.files.map((f) => f.path));
    renderFileList();
  });
  $("selectNone").addEventListener("click", () => {
    state.selectedFiles = new Set();
    renderFileList();
  });

  function renderFileList() {
    const list = $("fileList");
    const partial = state.scope === "partial";
    $("filesTools").classList.toggle("hidden", !partial);
    $("filesCount").textContent = String(state.folder.files.length);
    list.innerHTML = "";
    state.folder.files.forEach((f) => {
      const done = state.processedNames && state.processedNames.has(f.name);
      const row = document.createElement(partial ? "label" : "div");
      row.className = "file-row" + (partial ? " check pick" : "") + (done ? " processed" : "");
      const checked = state.selectedFiles.has(f.path);
      row.innerHTML = `
        ${partial ? `<input type="checkbox" ${checked ? "checked" : ""}/>
          <span class="box">${icon('<path d="m5 12 5 5L20 7"/>', 12)}</span>` : ""}
        <span class="fname">${escapeHtml(f.name)}</span>
        <span class="fmeta">${f.kind} · ${fmtDur(f.duration)} · ${fmtSize(f.size)}</span>`;
      if (partial) {
        const cb = row.querySelector("input");
        cb.addEventListener("change", () => {
          if (cb.checked) state.selectedFiles.add(f.path);
          else state.selectedFiles.delete(f.path);
          updateSelCount();
        });
      }
      list.appendChild(row);
    });
    updateSelCount();
    applyFilesCollapsed();
  }

  function updateSelCount() {
    if (state.scope !== "partial" || !state.folder) return;
    $("selCount").textContent = `${state.selectedFiles.size} of ${state.folder.files.length}`;
  }

  // ---------- scan ----------
  $("scanBtn").addEventListener("click", runScan);
  let scanSource = null;

  async function runScan() {
    const folder = state.folder;
    if (!folder) return;
    let files = null;
    if (state.scope === "partial") {
      files = [...state.selectedFiles];
      if (!files.length) { toast("Select at least one file", "error"); return; }
    }
    const btn = $("scanBtn");
    btn.disabled = true;
    startScanUI();
    const minLen = Math.max(1, parseFloat($("minLen").value) || 25);
    try {
      const res = await post("/api/scan", { folder: folder.name, files, min_seconds: minLen });
      setScanFile(`${res.total_files} file${res.total_files === 1 ? "" : "s"} queued`);
      openScanStream();
    } catch (e) {
      toast(e.message, "error");
      endScanUI("error", e.message);
      btn.disabled = false;
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

    if (ev.stage === "result") {
      state.patterns = ev.patterns || [];
      renderPatterns();
      renderRemoveLabels();
      endScanUI("complete");
      const found = (ev.new_patterns || []).length;
      const matched = (ev.matched_patterns || []).length;
      const failed = ev.previews_failed || 0;
      if (failed) {
        toast(`Scanned ${ev.files_scanned} file(s) · ${failed} preview${failed === 1 ? "" : "s"} couldn't be built — use “Generate preview” to retry`, "error");
      } else {
        toast(`Scanned ${ev.files_scanned} file(s) · ${found} new · ${matched} matched`, "notice");
      }
    }
  }

  function closeScan() {
    state.scanDone = true;
    if (scanSource) { scanSource.close(); scanSource = null; }
    $("scanBtn").disabled = false;
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
    state.patterns.forEach((p) => wrap.appendChild(renderPattern(p)));
  }

  function renderPattern(p) {
    const el = document.createElement("div");
    el.className = "pattern " + p.label;
    el.dataset.id = p.id;

    const labelBtns = state.labels.map((l) =>
      `<button data-label="${l}" class="${l === p.label ? "active" : ""}">${l}</button>`).join("");

    const clips = p.clips.map((c) => renderClip(c)).join("");

    el.innerHTML = `
      <div class="pattern-head">
        <div class="pattern-title">
          <span class="status-verb">${p.label}</span>
          <span class="pattern-chips">
            <span class="chip-mono">${fmtDur(p.duration)}</span>
            <span class="chip-mono">${p.shows} show${p.shows === 1 ? "" : "s"}</span>
            <span class="chip-mono">${p.clips.length} clip${p.clips.length === 1 ? "" : "s"}</span>
          </span>
        </div>
        <div class="pattern-controls">
          <div class="pattern-labels">${labelBtns}</div>
          <button class="pattern-mini trim-btn" title="Trim boundaries">Trim</button>
          <button class="pattern-del" title="Delete pattern">Del</button>
        </div>
      </div>
      <div class="trim-panel hidden">
        <span class="trim-label">Trim every clip —</span>
        <label class="trim-field">start <input type="number" class="num-input trim-head" min="0" step="0.5" value="${p.head_trim || 0}"/>s</label>
        <label class="trim-field">end <input type="number" class="num-input trim-tail" min="0" step="0.5" value="${p.tail_trim || 0}"/>s</label>
        <button class="btn-ghost trim-apply">Apply to all ${p.clips.length}</button>
        <span class="trim-hint">Seconds to trim off the detected start &amp; end of every clip — also tightens future detection. Re-applying the same values changes nothing.</span>
      </div>
      <div class="clip-list">${clips}</div>`;

    el.querySelectorAll(".pattern-labels button").forEach((b) => {
      b.addEventListener("click", () => setLabel(p, b.dataset.label, el));
    });
    el.querySelector(".pattern-del").addEventListener("click", () => deletePattern(p.id));
    el.querySelector(".trim-btn").addEventListener("click", () => {
      const panel = el.querySelector(".trim-panel");
      panel.classList.toggle("hidden");
      el.querySelector(".trim-btn").classList.toggle("active", !panel.classList.contains("hidden"));
    });
    el.querySelector(".trim-apply").addEventListener("click", () => applyPatternTrim(p, el));
    el.querySelectorAll(".preview-btn").forEach((b) => {
      b.addEventListener("click", () => playClip(b, p));
    });
    el.querySelectorAll(".gen-btn").forEach((b) => {
      b.addEventListener("click", () => generatePreview(b));
    });
    el.querySelectorAll(".adjust-btn").forEach((b) => {
      const clip = p.clips.find((c) => c.id === Number(b.dataset.clip));
      b.addEventListener("click", () => toggleClipEditor(b, clip, p));
    });
    return el;
  }

  function renderClip(c) {
    const range = `${fmtDur(c.start)}–${fmtDur(c.end)}`;
    const action = c.has_preview
      ? `<button class="preview-btn" data-clip="${c.id}">▸ Preview</button>`
      : `<button class="preview-btn gen-btn" data-clip="${c.id}">Generate preview</button>`;
    return `
      <div class="clip" data-clip="${c.id}">
        <span class="clip-info"><span class="cfile">${escapeHtml(c.file_name)}</span> · <span class="crange">${range}</span></span>
        <div class="clip-actions">
          ${action}
          <button class="adjust-btn" data-clip="${c.id}" title="Adjust this clip's boundaries">✎</button>
        </div>
        <div class="clip-media" data-media="${c.id}"></div>
      </div>`;
  }

  async function applyPatternTrim(p, el) {
    const head = parseFloat(el.querySelector(".trim-head").value) || 0;
    const tail = parseFloat(el.querySelector(".trim-tail").value) || 0;
    if (head <= 0 && tail <= 0) { toast("Enter a start or end trim amount", "error"); return; }
    const btn = el.querySelector(".trim-apply");
    btn.disabled = true;
    try {
      const res = await post("/api/pattern/adjust", { pattern_id: p.id, head, tail });
      state.patterns = res.patterns || state.patterns;
      renderPatterns();
      renderRemoveLabels();
      toast(`Trimmed ${res.clips_adjusted} clip(s) — regenerate previews to verify`, "notice");
    } catch (e) {
      toast(e.message, "error");
      btn.disabled = false;
    }
  }

  function toggleClipEditor(btn, c, p) {
    const clipEl = btn.closest(".clip");
    const slot = clipEl.querySelector(".clip-media");
    if (slot.dataset.editor) {
      slot.innerHTML = ""; delete slot.dataset.editor; delete slot.dataset.loaded;
      btn.classList.remove("active");
      return;
    }
    const n = p.clips.length;
    slot.innerHTML = `
      <div class="clip-edit">
        <div class="edit-fields">
          <label>start <input type="number" class="num-input edit-start" step="0.1" min="0" value="${c.start}"/>s</label>
          <label>end <input type="number" class="num-input edit-end" step="0.1" min="0" value="${c.end}"/>s</label>
          <button class="btn-ghost save-bounds">Save &amp; preview</button>
          ${n > 1 ? `<button class="btn-ghost apply-all">Apply to all ${n}</button>` : ""}
        </div>
        ${n > 1 ? `<p class="edit-hint"><em>Apply to all.</em> Trim every clip of this pattern by the same amount you trimmed here — and tighten future detection to match.</p>` : ""}
        <div class="edit-player"></div>
      </div>`;
    slot.dataset.editor = "1";
    btn.classList.add("active");
    slot.querySelector(".save-bounds").addEventListener("click", () => saveClipBounds(clipEl, c, slot));
    const applyBtn = slot.querySelector(".apply-all");
    if (applyBtn) applyBtn.addEventListener("click", () => applyClipToAll(c, p, slot));
  }

  async function applyClipToAll(c, p, slot) {
    const start = parseFloat(slot.querySelector(".edit-start").value);
    const end = parseFloat(slot.querySelector(".edit-end").value);
    if (!(end - start >= 0.2)) { toast("End must be at least 0.2s after start", "error"); return; }
    const btn = slot.querySelector(".apply-all");
    btn.disabled = true; btn.textContent = "Applying…";
    try {
      const res = await post("/api/clip/propagate", { clip_id: c.id, start, end });
      state.patterns = res.patterns || state.patterns;
      renderPatterns();
      renderRemoveLabels();
      const trim = [];
      if (res.head) trim.push(`${res.head}s off the start`);
      if (res.tail) trim.push(`${res.tail}s off the end`);
      toast(`Trimmed ${trim.join(" and ") || "boundaries"} on all ${res.clips_adjusted} clip(s)`, "notice");
    } catch (e) {
      toast(e.message, "error");
      btn.disabled = false; btn.textContent = `Apply to all ${p.clips.length}`;
    }
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

  async function generatePreview(btn) {
    const clipId = btn.dataset.clip;
    const prev = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Generating…";
    try {
      await post("/api/preview", { clip_id: Number(clipId) });
      // Mark the clip as having a preview and turn this into a Play button.
      state.patterns.forEach((p) => p.clips.forEach((c) => {
        if (c.id === Number(clipId)) c.has_preview = true;
      }));
      btn.classList.remove("gen-btn");
      btn.disabled = false;
      btn.textContent = "▸ Preview";
      btn.replaceWith(btn.cloneNode(true)); // drop old listener
      const fresh = document.querySelector(`.preview-btn[data-clip="${clipId}"]`);
      if (fresh) fresh.addEventListener("click", () => playClip(fresh, null));
    } catch (e) {
      btn.disabled = false;
      btn.textContent = prev;
      toast(e.message, "error");
    }
  }

  function playClip(btn, pattern) {
    const clipId = btn.dataset.clip;
    const slot = btn.closest(".clip").querySelector(".clip-media");
    if (slot.dataset.loaded) {
      slot.innerHTML = ""; delete slot.dataset.loaded; btn.textContent = "▸ Preview"; return;
    }
    const isVideo = state.folder && state.folder.kind === "video";
    const tag = isVideo ? "video" : "audio";
    slot.innerHTML = `<${tag} controls autoplay src="/api/preview/${clipId}"></${tag}>`;
    slot.dataset.loaded = "1";
    btn.textContent = "▾ Hide";
  }

  async function setLabel(p, label, el) {
    try {
      await post("/api/label", { pattern_id: p.id, label });
      p.label = label;
      el.className = "pattern " + label;
      el.querySelector(".status-verb").textContent = label;
      el.querySelectorAll(".pattern-labels button").forEach((b) =>
        b.classList.toggle("active", b.dataset.label === label));
      renderRemoveLabels();
    } catch (e) { toast(e.message, "error"); }
  }

  async function deletePattern(id) {
    try {
      await api(`/api/pattern/${id}`, { method: "DELETE" });
      state.patterns = state.patterns.filter((p) => p.id !== id);
      renderPatterns();
      renderRemoveLabels();
      toast("Pattern removed", "notice");
    } catch (e) { toast(e.message, "error"); }
  }

  // ---------- remove the fluff ----------
  function renderRemoveLabels() {
    const present = new Set(state.patterns.map((p) => p.label));
    const grid = $("removeLabels");
    grid.innerHTML = "";
    state.labels.forEach((l) => {
      const count = state.patterns.filter((p) => p.label === l).length;
      const label = document.createElement("label");
      label.className = "check";
      const on = state.removeLabels.has(l);
      label.innerHTML = `
        <input type="checkbox" ${on ? "checked" : ""} ${count ? "" : "disabled"}/>
        <span class="box">${icon('<path d="m5 12 5 5L20 7"/>', 12)}</span>
        <span class="clabel">${l} <span class="mono" style="color:var(--text-faint);font-size:0.75em">(${count})</span></span>`;
      const cb = label.querySelector("input");
      cb.addEventListener("change", () => {
        if (cb.checked) state.removeLabels.add(l); else state.removeLabels.delete(l);
        updateRemoveSummary();
      });
      grid.appendChild(label);
    });
    updateRemoveSummary();
  }

  function updateRemoveSummary() {
    const labels = [...state.removeLabels];
    const clips = state.patterns
      .filter((p) => labels.includes(p.label))
      .reduce((n, p) => n + p.clips.length, 0);
    $("removeSummary").textContent = clips
      ? `${clips} segment${clips === 1 ? "" : "s"} across ${labels.join(", ")} will be cut`
      : "Nothing selected to remove";
    $("removeBtn").disabled = !clips;
  }

  let removeConfirm = false;
  let removeConfirmTimer = null;
  $("removeBtn").addEventListener("click", () => {
    const btn = $("removeBtn");
    if (!removeConfirm) {
      removeConfirm = true;
      btn.textContent = "Tap again to confirm";
      removeConfirmTimer = setTimeout(() => {
        removeConfirm = false; btn.textContent = "Remove the fluff";
      }, 3000);
      return;
    }
    clearTimeout(removeConfirmTimer);
    removeConfirm = false; btn.textContent = "Remove the fluff";
    runRemove();
  });

  async function runRemove() {
    const labels = [...state.removeLabels];
    if (!labels.length) return;
    const btn = $("removeBtn");
    btn.disabled = true;
    try {
      const res = await post("/api/remove", { folder: state.folder.name, labels });
      renderRemoveResults(res.results);
      const saved = res.results.reduce((s, r) => s + (r.saved_sec || 0), 0);
      toast(`Trimmed ${res.results.length} file(s) · saved ${fmtDur(saved)}`, "notice");
      refreshProcessed();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      btn.disabled = false;
    }
  }

  function renderRemoveResults(results) {
    const wrap = $("removeResults");
    if (!results || !results.length) { wrap.innerHTML = ""; return; }
    wrap.innerHTML = results.map((r) =>
      r.error
        ? `<div class="remove-result"><span class="err">✕ ${escapeHtml(r.file)}</span><span>${escapeHtml(r.error)}</span></div>`
        : `<div class="remove-result"><span class="ok">✓ ${escapeHtml(r.file)}</span>
           <span>saved ${fmtDur(r.saved_sec)} · ${r.segments} cut → ${escapeHtml(r.output)}</span></div>`
    ).join("");
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

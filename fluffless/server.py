"""The Fluffless local server.

A single stdlib HTTP server (no third-party dependencies) that serves the
self-contained web UI and a small JSON API, plus range-capable streaming of
media and preview clips so playback works in the browser. Local-first by
design: it binds to 127.0.0.1 and only ever reads inside the chosen library.
"""

from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import queue
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .binaries import detect_tools
from .clips import OUT_DIR, extract_preview, remove_segments
from .db import STATUSES, Database
from .media import scan_library
from .repetition import DetectParams
from .scan import (
    absorb_overlapping_pending,
    apply_pattern_to_stored,
    relocate_group_from_clip,
    scan_folder,
)

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
PREVIEW_DIR = "previews"

# How the scan's phases divide the 0–100% bar. Fingerprinting (reading every
# file) is the bulk and the part with a clean denominator, so it owns most of
# the bar and is where the ETA comes from; detection + previews are the tail.
FP_SHARE = 0.80
DETECT_AT = 82.0
DETECT_END = 97.0


class ScanJob:
    """A single in-flight scan. Progress events are buffered in a queue so the
    SSE endpoint can stream them even if it connects a moment after the start."""

    def __init__(self, folder: str, total_files: int) -> None:
        self.folder = folder
        self.total_files = total_files
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.started = time.time()
        self.previews = 0
        self.previews_failed = 0
        self.detect_started: float | None = None
        self.finished = False

    def put(self, event: dict) -> None:
        self.events.put(event)


class RemoveJob:
    """A single in-flight 'Remove the Fluff' run, streamed like a scan."""

    def __init__(self, total_files: int) -> None:
        self.total_files = total_files
        self.events: "queue.Queue[dict]" = queue.Queue()
        self.started = time.time()
        self.finished = False

    def put(self, event: dict) -> None:
        self.events.put(event)


def _enrich(payload: dict, job: ScanJob) -> dict:
    """Turn a raw engine event into a UI event with percent, ETA, and a line of
    human copy. Keeps the engine (scan.py) free of any timing concerns."""
    stage = payload.get("stage")
    ev = dict(payload)
    elapsed = time.time() - job.started

    if stage == "fingerprint":
        idx = payload.get("index", 0)
        total = payload.get("total", job.total_files) or 1
        ev["percent"] = round((idx / total) * FP_SHARE * 100, 1)
        ev["message"] = f"Fingerprinting · {payload.get('file', '')}"
        ev["detail"] = f"{idx + 1} of {total}"
        if idx >= 1:                       # need at least one finished file to estimate
            per_file = elapsed / idx
            ev["eta_seconds"] = round(per_file * (total - idx))
    elif stage == "detect":
        job.detect_started = time.time()
        ev["percent"] = DETECT_AT
        ev["message"] = f"Finding recurring segments across {payload.get('count', 0)} files"
    elif stage == "detect_progress":
        done = payload.get("done", 0)
        total = payload.get("total", 1) or 1
        frac = done / total
        ev["percent"] = round(DETECT_AT + frac * (DETECT_END - DETECT_AT), 1)
        ev["message"] = "Finding recurring segments"
        ev["detail"] = f"{done:,} of {total:,} comparisons"
        if job.detect_started is None:
            job.detect_started = time.time()
        det_elapsed = time.time() - job.detect_started
        if done >= 1:
            ev["eta_seconds"] = round((det_elapsed / done) * (total - done))
    elif stage == "matched":
        ev["message"] = f"Matched a confirmed segment · {payload.get('file', '')}"
    elif stage == "found":
        ev["message"] = f"New segment · {payload.get('file', '')}"
    elif stage == "normalize":
        ev["percent"] = DETECT_END
        ev["message"] = "Aligning segment lengths"
    elif stage == "preview":
        job.previews += 1
        ev["percent"] = round(min(99.0, DETECT_END + job.previews * 0.2), 1)
        ev["message"] = f"Extracting preview · {payload.get('file', '')}"
    elif stage == "error":
        ev["message"] = f"Skipped {payload.get('file', '')}: {payload.get('message', '')}"
    elif stage == "done":
        ev["percent"] = 99.0
        ev["message"] = "Finalising"
    # "warn" passes through untouched — it already carries its message.
    return ev


def patterns_payload(db: Database, folder: str | None) -> list[dict]:
    """Serialise patterns (with their clips) for the UI. Shared by the patterns
    endpoint and the scan-job result event."""
    out = []
    for row in db.patterns(folder):
        keys = row.keys()
        item_sec = row["item_sec"] or 0.1238
        head_items = row["head_items"] if "head_items" in keys else 0
        tail_items = row["tail_items"] if "tail_items" in keys else 0
        clips = [
            {
                "id": c["id"],
                "file_name": c["file_name"],
                "file_path": c["file_path"],
                "start": round(c["start"], 2),
                "end": round(c["end"], 2),
                "edited": abs((c["start"] or 0) - (c["orig_start"] if c["orig_start"] is not None else c["start"])) > 0.05
                          or abs((c["end"] or 0) - (c["orig_end"] if c["orig_end"] is not None else c["end"])) > 0.05,
                "has_preview": bool(c["preview"]),
            }
            for c in db.clips(row["id"])
        ]
        out.append({
            "id": row["id"],
            "folder": row["folder"],
            "status": row["status"] if "status" in keys else "pending",
            "duration": round(row["duration"], 2),
            "shows": row["shows"],
            "bits": row["bits"],
            "head_trim": round(head_items * item_sec, 2),
            "tail_trim": round(tail_items * item_sec, 2),
            "pinned": bool(row["pinned"]) if "pinned" in keys else False,
            "clips": clips,
        })
    return out


class AppState:
    """Server-wide state: the active library, its database, and a folder cache."""

    def __init__(self, workers: int = 1) -> None:
        self.tools = detect_tools()
        self.library: str | None = None
        self.db: Database | None = None
        self.folders: list = []
        self.lock = threading.Lock()
        self.scan_job: ScanJob | None = None
        self.scan_lock = threading.Lock()
        self.remove_job: RemoveJob | None = None
        self.remove_lock = threading.Lock()
        self.workers = max(1, workers)

    def open_library(self, path: str) -> dict:
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            raise FileNotFoundError(f"Not a folder: {path}")
        if self.db:
            self.db.close()
        self.library = path
        self.db = Database.open(path)
        self.folders = scan_library(path, self.tools)
        os.makedirs(os.path.join(self.db.workspace, PREVIEW_DIR), exist_ok=True)
        return {
            "library": path,
            "folders": [f.to_dict() for f in self.folders],
        }

    def folder(self, name: str):
        for f in self.folders:
            if f.name == name:
                return f
        return None

    def preview_dir(self) -> str:
        assert self.db
        return os.path.join(self.db.workspace, PREVIEW_DIR)

    def is_within_library(self, path: str) -> bool:
        if not self.library:
            return False
        path = os.path.abspath(path)
        return (
            path == self.library
            or path.startswith(self.library + os.sep)
        )


class Handler(BaseHTTPRequestHandler):
    server_version = "Fluffless/1.0"
    state: AppState  # injected on the server instance

    # --- helpers -------------------------------------------------------------

    def _json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def _safe_error(self, message: str, status: int = 500) -> None:
        """Report an error, but never raise if the socket is already gone."""
        try:
            self._error(message, status)
        except (ConnectionError, OSError):
            pass

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def log_message(self, *args) -> None:  # quiet by default
        pass

    # --- routing -------------------------------------------------------------

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/" or path == "/index.html":
                return self._serve_static("index.html")
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/"):])
            if path == "/api/status":
                return self._api_status()
            if path == "/api/folders":
                return self._api_folders()
            if path == "/api/scan/stream":
                return self._api_scan_stream()
            if path == "/api/remove/stream":
                return self._api_remove_stream()
            if path == "/api/patterns":
                return self._api_patterns(qs)
            if path == "/api/processed":
                return self._api_processed()
            if path == "/api/export":
                return self._api_export(qs)
            if path == "/api/media":
                return self._serve_media(qs)
            m = re.match(r"^/api/preview/(\d+)$", path)
            if m:
                return self._serve_preview(int(m.group(1)))
            return self._error("not found", 404)
        except ConnectionError:
            pass  # client hung up (incl. Windows WinError 10053) — nothing to send
        except Exception as exc:  # noqa: BLE001
            self._safe_error(f"{type(exc).__name__}: {exc}")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/library":
                return self._api_open_library()
            if path == "/api/scan":
                return self._api_scan()
            if path == "/api/pattern/review":
                return self._api_pattern_review()
            if path == "/api/remove":
                return self._api_remove()
            if path == "/api/preview":
                return self._api_make_preview()
            if path == "/api/clip/adjust":
                return self._api_clip_adjust()
            if path == "/api/clip/propagate":
                return self._api_clip_propagate()
            if path == "/api/clip/relocate":
                return self._api_clip_relocate()
            if path == "/api/clip/reset":
                return self._api_clip_reset()
            if path == "/api/clip/move":
                return self._api_clip_move()
            if path == "/api/pattern/adjust":
                return self._api_pattern_adjust()
            if path == "/api/pattern/reset":
                return self._api_pattern_reset()
            if path == "/api/pattern/fingerprint":
                return self._api_pattern_fingerprint()
            return self._error("not found", 404)
        except ConnectionError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._safe_error(f"{type(exc).__name__}: {exc}")

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        m = re.match(r"^/api/pattern/(\d+)$", parsed.path)
        if m and self.state.db:
            self.state.db.delete_pattern(int(m.group(1)))
            return self._json({"ok": True})
        return self._error("not found", 404)

    # --- API: status & library ----------------------------------------------

    def _api_status(self) -> None:
        st = self.state
        self._json({
            "tools": st.tools.status(),
            "library": st.library,
            "statuses": list(STATUSES),
            "workers": st.workers,
            "folders": [f.to_dict() for f in st.folders] if st.library else [],
        })

    def _api_open_library(self) -> None:
        body = self._body()
        path = body.get("path")
        if not path:
            return self._error("path required")
        try:
            data = self.state.open_library(path)
        except FileNotFoundError as exc:
            return self._error(str(exc), 404)
        self._json(data)

    def _api_folders(self) -> None:
        self._json({"folders": [f.to_dict() for f in self.state.folders]})

    # --- API: scan (non-blocking, with a streamed progress feed) -------------

    def _api_scan(self) -> None:
        """Start a scan in the background and return immediately. Progress is
        delivered over the SSE endpoint ``/api/scan/stream``."""
        st = self.state
        if not st.db or not st.library:
            return self._error("open a library first")
        with st.scan_lock:
            if st.scan_job and not st.scan_job.finished:
                return self._error("a scan is already running", 409)

            body = self._body()
            folder = st.folder(body.get("folder"))
            if not folder:
                return self._error(f"unknown folder: {body.get('folder')}", 404)

            chosen = body.get("files")  # optional subset for a partial scan
            files = folder.files
            if chosen:
                wanted = set(chosen)
                files = [f for f in folder.files if f.path in wanted]
            if not files:
                return self._error("no files selected")

            params = _params_from(body)
            try:
                workers = int(body.get("workers") or st.workers)
            except (TypeError, ValueError):
                workers = st.workers
            workers = max(1, workers)
            job = ScanJob(folder.name, len(files))
            st.scan_job = job

        thread = threading.Thread(
            target=self._run_scan_job, args=(job, folder, files, params, workers), daemon=True,
        )
        thread.start()
        self._json({"ok": True, "folder": folder.name, "total_files": len(files), "workers": workers})

    def _run_scan_job(self, job: ScanJob, folder, files, params, workers: int = 1) -> None:
        """Worker thread: runs the scan, pushing enriched progress events into
        the job queue, then a final ``result`` event and an ``end`` sentinel."""
        st = self.state

        # Previews are built on demand (on first play), not during the scan:
        # length-normalisation would invalidate any pre-built ones anyway, and
        # skipping them keeps scans fast on large libraries.
        def progress(payload: dict) -> None:
            job.put(_enrich(payload, job))

        try:
            with st.lock:
                result = scan_folder(
                    st.db, st.library, folder.name, files, st.tools,
                    params=params, progress=progress, make_preview=None,
                    workers=workers,
                )
            job.put({
                "stage": "result",
                "percent": 100.0,
                "message": "Scan complete",
                "folder": folder.name,
                "files_scanned": result.files_scanned,
                "new_patterns": result.new_patterns,
                "matched_patterns": result.matched_patterns,
                "clips_added": result.clips_added,
                "previews_failed": job.previews_failed,
                "patterns": patterns_payload(st.db, folder.name),
            })
        except Exception as exc:  # noqa: BLE001
            job.put({"stage": "fatal", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            job.finished = True            # set before the sentinel so a
            job.put({"stage": "end"})  # client that sees "end" can start the next run

    def _api_scan_stream(self) -> None:
        """Server-Sent Events feed for the active scan job."""
        self._stream_job(self.state.scan_job, "no scan running")

    def _stream_job(self, job, missing: str) -> None:
        """Generic SSE feed: drain a job's event queue until its ``end``."""
        if not job:
            return self._error(missing, 404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            while True:
                try:
                    ev = job.events.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")   # heartbeat keeps the socket open
                    self.wfile.flush()
                    continue
                self.wfile.write(b"data: " + json.dumps(ev).encode("utf-8") + b"\n\n")
                self.wfile.flush()
                if ev.get("stage") == "end":
                    break
        except (ConnectionError, OSError):
            pass  # client closed the stream (refresh, navigate, WinError 10053)

    # --- API: patterns & review ----------------------------------------------

    def _api_patterns(self, qs) -> None:
        if not self.state.db:
            return self._error("open a library first")
        folder = (qs.get("folder") or [None])[0]
        self._json({"patterns": patterns_payload(self.state.db, folder)})

    def _api_pattern_review(self) -> None:
        """Record the user's verdict on a detected segment. ``decision`` is one
        of ``ad`` (confirm — approve for removal), ``not_ad`` (dismiss), or
        ``pending`` (undo). Confirming an ad immediately re-parses every cached
        file for *all* its occurrences, so by the time the user removes, the
        confirmed segment is found everywhere it airs — including repeats."""
        if not self.state.db:
            return self._error("open a library first")
        body = self._body()
        pid = body.get("pattern_id")
        decision = body.get("decision")
        mapping = {"ad": "confirmed", "not_ad": "dismissed", "pending": "pending"}
        status = mapping.get(decision)
        if status is None:
            return self._error("decision must be one of: ad, not_ad, pending")
        st = self.state
        row = st.db.pattern(int(pid))
        if not row:
            return self._error("unknown segment", 404)
        st.db.set_status(int(pid), status)
        # Confirming turns the fingerprint into a known signature: re-parse every
        # cached file in the folder for every occurrence (locate_all), tagging
        # airings that pre-dated the confirmation. Only on confirm.
        applied = 0
        absorbed = 0
        if status == "confirmed":
            applied = apply_pattern_to_stored(st.db, int(pid))
            absorbed = len(absorb_overlapping_pending(st.db, int(pid)))
        self._json({
            "ok": True,
            "status": status,
            "applied_to": applied,
            "absorbed": absorbed,
            "patterns": patterns_payload(st.db, row["folder"]),
        })

    # --- API: remove the fluff -----------------------------------------------

    def _api_remove(self) -> None:
        """Start a 'Remove the Fluff' run in the background; progress streams
        over ``/api/remove/stream``."""
        st = self.state
        if not st.db or not st.library:
            return self._error("open a library first")
        if not st.tools.has_ffmpeg:
            return self._error("ffmpeg is not installed", 503)
        with st.remove_lock:
            if st.remove_job and not st.remove_job.finished:
                return self._error("a removal is already running", 409)

            body = self._body()
            folder = st.folder(body.get("folder"))
            if not folder:
                return self._error("unknown folder", 404)
            chosen = set(body.get("files") or [])

            # Every occurrence of every *confirmed* segment, grouped per file —
            # so a file with several ads has them all cut in one pass. Removal
            # acts only on what the user approved; pending/dismissed are skipped.
            per_file: dict[str, list[tuple[float, float]]] = {}
            for row in st.db.patterns(folder.name):
                if row["status"] != "confirmed":
                    continue
                for c in st.db.clips(row["id"]):
                    if chosen and c["file_path"] not in chosen:
                        continue
                    per_file.setdefault(c["file_path"], []).append(
                        (c["start"], c["end"])
                    )
            if not per_file:
                return self._error("no confirmed segments to remove")

            try:
                workers = int(body.get("workers") or st.workers)
            except (TypeError, ValueError):
                workers = st.workers
            job = RemoveJob(len(per_file))
            st.remove_job = job

        thread = threading.Thread(
            target=self._run_remove_job, args=(job, folder, per_file, max(1, workers)), daemon=True,
        )
        thread.start()
        self._json({"ok": True, "total_files": len(per_file), "workers": max(1, workers)})

    def _run_remove_job(self, job: RemoveJob, folder, per_file: dict, workers: int) -> None:
        st = self.state
        out_dir = os.path.join(folder.path, OUT_DIR)
        file_by_path = {f.path: f for f in folder.files}
        items = list(per_file.items())
        total = len(items)

        def work(fpath: str, segs: list):
            mf = file_by_path.get(fpath)
            duration = mf.duration if mf else _max_end(segs)
            kind = mf.kind if mf else None
            ranges = sorted((s, e) for s, e in segs)
            out = remove_segments(fpath, ranges, duration, out_dir, st.tools, kind)
            saved = sum(e - s for s, e in ranges)
            return out, saved

        results: list[dict] = []
        done = 0
        job.put({"stage": "start", "percent": 0.0, "total": total,
                 "message": f"Trimming {total} file(s)"})
        try:
            with ThreadPoolExecutor(max_workers=min(workers, total)) as ex:
                futures = {ex.submit(work, fp, segs): (fp, segs) for fp, segs in items}
                for fut in as_completed(futures):
                    fpath, segs = futures[fut]
                    name = os.path.basename(fpath)
                    done += 1
                    try:
                        out, saved = fut.result()
                        st.db.add_processed(
                            fpath, out,
                            [{"start": round(s, 2), "end": round(e, 2)} for s, e in segs],
                            saved,
                        )
                        res = {"file": name, "output": os.path.relpath(out, folder.path),
                               "saved_sec": round(saved, 2), "segments": len(segs)}
                        msg = f"Trimmed {name}"
                    except Exception as exc:  # noqa: BLE001
                        res = {"file": name, "error": str(exc)}
                        msg = f"Failed: {name}"
                    results.append(res)
                    job.put({"stage": "file", "percent": round(done / total * 100, 1),
                             "done": done, "total": total, "message": msg, "result": res})
            ok = sum(1 for r in results if "error" not in r)
            job.put({"stage": "result", "percent": 100.0,
                     "message": f"Removed fluff from {ok}/{total} file(s)",
                     "results": results, "out_dir": os.path.relpath(out_dir, st.library)})
        except Exception as exc:  # noqa: BLE001
            job.put({"stage": "fatal", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            job.finished = True            # set before the sentinel so a
            job.put({"stage": "end"})  # client that sees "end" can start the next run

    def _api_remove_stream(self) -> None:
        """Server-Sent Events feed for the active removal job."""
        self._stream_job(self.state.remove_job, "no removal running")

    def _api_make_preview(self) -> None:
        """(Re)build a preview clip on demand for a stored clip."""
        st = self.state
        if not st.db:
            return self._error("open a library first")
        if not st.tools.has_ffmpeg:
            return self._error("ffmpeg is not installed", 503)
        body = self._body()
        clip = st.db.clip(int(body.get("clip_id")))
        if not clip:
            return self._error("unknown clip", 404)
        if not os.path.isfile(clip["file_path"]):
            return self._error(f"source file is missing: {clip['file_name']}", 404)
        try:
            out = extract_preview(
                clip["file_path"], clip["start"], clip["end"], st.preview_dir(), st.tools,
            )
        except Exception as exc:  # noqa: BLE001
            return self._error(f"Could not build preview: {exc}", 500)
        rel = os.path.relpath(out, st.preview_dir())
        st.db.set_clip_preview(clip["id"], rel)
        self._json({"ok": True, "clip_id": clip["id"]})

    def _api_clip_adjust(self) -> None:
        """Set one clip's exact start/end and rebuild its preview, so the user
        can fine-tune a single occurrence and hear the result immediately."""
        st = self.state
        if not st.db:
            return self._error("open a library first")
        body = self._body()
        clip = st.db.clip(int(body.get("clip_id")))
        if not clip:
            return self._error("unknown clip", 404)
        try:
            start = max(0.0, float(body.get("start")))
            end = float(body.get("end"))
        except (TypeError, ValueError):
            return self._error("start and end must be numbers")
        if end - start < 0.2:
            return self._error("end must be at least 0.2s after start")
        st.db.update_clip_bounds(clip["id"], start, end)

        preview_ok, message = self._rebuild_preview(clip["id"])
        self._json({
            "ok": True,
            "clip_id": clip["id"],
            "start": round(start, 2),
            "end": round(end, 2),
            "has_preview": preview_ok,
            "preview_error": None if preview_ok else message,
        })

    def _api_clip_propagate(self) -> None:
        """Apply one refined clip's correction to every clip of its pattern.

        Optionally sets the reference clip's bounds first (so the user can
        refine and propagate in one action), then shifts all clips to their
        detected bounds trimmed by the same head/tail — and tightens the
        fingerprint so future scans match just as tightly."""
        st = self.state
        if not st.db:
            return self._error("open a library first")
        body = self._body()
        clip = st.db.clip(int(body.get("clip_id")))
        if not clip:
            return self._error("unknown clip", 404)

        if body.get("start") is not None and body.get("end") is not None:
            try:
                start = max(0.0, float(body["start"]))
                end = float(body["end"])
            except (TypeError, ValueError):
                return self._error("start and end must be numbers")
            if end - start < 0.2:
                return self._error("end must be at least 0.2s after start")
            st.db.update_clip_bounds(clip["id"], start, end)

        result = st.db.propagate_from_clip(clip["id"])
        if result is None:
            return self._error("unknown clip", 404)
        n, head, tail = result
        pattern = st.db.pattern(clip["pattern_id"])
        self._json({
            "ok": True,
            "clips_adjusted": n,
            "head": round(head, 2),
            "tail": round(tail, 2),
            "patterns": patterns_payload(st.db, pattern["folder"] if pattern else None),
        })

    def _api_clip_relocate(self) -> None:
        """Find one clip's cropped segment across the whole folder and re-derive
        its group from it: snap every match to the same length, pull in episodes
        that weren't grouped, and move clips that no longer match into their own
        group. Optionally crops the reference clip first, in one action."""
        st = self.state
        if not st.db:
            return self._error("open a library first")
        body = self._body()
        clip = st.db.clip(int(body.get("clip_id")))
        if not clip:
            return self._error("unknown clip", 404)
        split_nonmatches = False
        if body.get("start") is not None and body.get("end") is not None:
            try:
                start = max(0.0, float(body["start"]))
                end = float(body["end"])
            except (TypeError, ValueError):
                return self._error("start and end must be numbers")
            if end - start < 0.2:
                return self._error("end must be at least 0.2s after start")
            # Only split non-matches when the user actually cropped the clip —
            # i.e. meaningfully different from the detected bounds. Without an
            # explicit crop the operation is pure discovery: snap + pull in,
            # never evict an existing clip.
            orig_s = clip["orig_start"] if clip["orig_start"] is not None else clip["start"]
            orig_e = clip["orig_end"] if clip["orig_end"] is not None else clip["end"]
            if abs(start - orig_s) > 1.0 or abs(end - orig_e) > 1.0:
                split_nonmatches = True
            st.db.update_clip_bounds(clip["id"], start, end)
            self._rebuild_preview(clip["id"])
        res = relocate_group_from_clip(st.db, clip["id"], split_nonmatches=split_nonmatches)
        if res is None:
            return self._error("unknown clip", 404)
        if "error" in res:
            return self._error(res["error"])
        self._json({
            "ok": True,
            "snapped": res["snapped"],
            "added": res["added"],
            "moved_out": res["moved_out"],
            "duration": round(res["duration"], 2),
            "patterns": patterns_payload(st.db, res["folder"]),
        })

    def _api_pattern_adjust(self) -> None:
        """Trim head/tail seconds off every clip of a pattern (and the stored
        fingerprint), then return the refreshed folder patterns."""
        st = self.state
        if not st.db:
            return self._error("open a library first")
        body = self._body()
        row = st.db.pattern(int(body.get("pattern_id")))
        if not row:
            return self._error("unknown pattern", 404)
        try:
            head = float(body.get("head", 0) or 0)
            tail = float(body.get("tail", 0) or 0)
        except (TypeError, ValueError):
            return self._error("head and tail must be numbers")
        if head < 0 or tail < 0:
            return self._error("trim amounts must be 0 or more")
        if head == 0 and tail == 0:
            return self._error("nothing to trim")
        n = st.db.trim_pattern(row["id"], head, tail)
        self._json({
            "ok": True,
            "clips_adjusted": n,
            "patterns": patterns_payload(st.db, row["folder"]),
        })

    def _api_pattern_fingerprint(self) -> None:
        """Adopt one clip's (possibly hand-cropped) region as the pattern's saved
        fingerprint, so future scans match just that — not the surrounding
        content. Optionally crops the clip first, in one action."""
        st = self.state
        if not st.db:
            return self._error("open a library first")
        body = self._body()
        clip = st.db.clip(int(body.get("clip_id")))
        if not clip:
            return self._error("unknown clip", 404)
        if body.get("start") is not None and body.get("end") is not None:
            try:
                start = max(0.0, float(body["start"]))
                end = float(body["end"])
            except (TypeError, ValueError):
                return self._error("start and end must be numbers")
            if end - start < 0.2:
                return self._error("end must be at least 0.2s after start")
            st.db.update_clip_bounds(clip["id"], start, end)
            self._rebuild_preview(clip["id"])
        res = st.db.set_fingerprint_from_clip(clip["id"])
        if res is None:
            return self._error("unknown clip", 404)
        if "error" in res:
            return self._error(res["error"])
        pattern = st.db.pattern(clip["pattern_id"])
        self._json({
            "ok": True,
            "duration": round(res["duration"], 2),
            "patterns": patterns_payload(st.db, pattern["folder"] if pattern else None),
        })

    def _api_pattern_reset(self) -> None:
        """Reset a pattern to its detected default: baseline fingerprint, no
        head/tail trim, and every clip back to its detected bounds."""
        st = self.state
        if not st.db:
            return self._error("open a library first")
        body = self._body()
        row = st.db.pattern(int(body.get("pattern_id")))
        if not row:
            return self._error("unknown pattern", 404)
        n = st.db.reset_pattern(row["id"])
        self._json({
            "ok": True,
            "clips_reset": n or 0,
            "patterns": patterns_payload(st.db, row["folder"]),
        })

    def _api_clip_reset(self) -> None:
        """Return one clip to its detected bounds and rebuild its preview."""
        st = self.state
        if not st.db:
            return self._error("open a library first")
        body = self._body()
        clip = st.db.clip(int(body.get("clip_id")))
        if not clip:
            return self._error("unknown clip", 404)
        res = st.db.reset_clip(clip["id"])
        if res is None:
            return self._error("unknown clip", 404)
        start, end = res
        preview_ok, message = self._rebuild_preview(clip["id"])
        self._json({
            "ok": True,
            "clip_id": clip["id"],
            "start": round(start, 2),
            "end": round(end, 2),
            "has_preview": preview_ok,
            "preview_error": None if preview_ok else message,
        })

    def _api_clip_move(self) -> None:
        """Move one clip to another group, or split it into a new one — for
        correcting a mis-grouping. With ``target_pattern_id`` it reassigns;
        without one (or "new") it creates a fresh group from the clip."""
        st = self.state
        if not st.db:
            return self._error("open a library first")
        body = self._body()
        clip = st.db.clip(int(body.get("clip_id")))
        if not clip:
            return self._error("unknown clip", 404)
        target = body.get("target_pattern_id")
        if target in (None, "", "new"):
            res = st.db.new_group_from_clip(clip["id"], status="pending")
        else:
            src = st.db.pattern(clip["pattern_id"])
            tgt = st.db.pattern(int(target))
            if not tgt:
                return self._error("unknown target group", 404)
            if src and tgt["folder"] != src["folder"]:
                return self._error("can only move within the same folder")
            res = st.db.move_clip(clip["id"], int(target))
        if res is None:
            return self._error("unknown clip", 404)
        if "error" in res:
            return self._error(res["error"])
        self._json({
            "ok": True,
            "result": res,
            "patterns": patterns_payload(st.db, res.get("folder")),
        })

    def _rebuild_preview(self, clip_id: int) -> tuple[bool, str | None]:
        """Regenerate a clip's preview after its bounds change. Returns
        (ok, error_message)."""
        st = self.state
        if not st.tools.has_ffmpeg:
            return False, "ffmpeg is not installed"
        clip = st.db.clip(clip_id)
        if not clip or not os.path.isfile(clip["file_path"]):
            return False, "source file is missing"
        try:
            out = extract_preview(
                clip["file_path"], clip["start"], clip["end"], st.preview_dir(), st.tools,
            )
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        st.db.set_clip_preview(clip["id"], os.path.relpath(out, st.preview_dir()))
        return True, None

    # --- API: processed & export ---------------------------------------------

    def _api_processed(self) -> None:
        if not self.state.db:
            return self._error("open a library first")
        rows = [
            {
                "file_name": r["file_name"],
                "output_path": r["output_path"],
                "saved_sec": round(r["saved_sec"], 2),
                "segments": len(json.loads(r["removed"] or "[]")),
            }
            for r in self.state.db.processed()
        ]
        self._json({"processed": rows})

    def _api_export(self, qs) -> None:
        if not self.state.db:
            return self._error("open a library first")
        fmt = (qs.get("format") or ["json"])[0]
        if fmt == "md" or fmt == "markdown":
            body = self.state.db.export_markdown().encode("utf-8")
            ctype, fname = "text/markdown", "fluffless.md"
        else:
            body = self.state.db.export_json().encode("utf-8")
            ctype, fname = "application/json", "fluffless.json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --- static & media serving ----------------------------------------------

    def _serve_static(self, rel: str) -> None:
        rel = posixpath.normpath(rel).lstrip("/")
        if rel.startswith(".."):
            return self._error("forbidden", 403)
        full = os.path.join(WEB_DIR, rel)
        if not os.path.isfile(full):
            return self._error("not found", 404)
        self._send_file(full)

    def _serve_preview(self, clip_id: int) -> None:
        st = self.state
        if not st.db:
            return self._error("open a library first")
        clip = st.db.clip(clip_id)
        if not clip or not clip["preview"]:
            return self._error("no preview", 404)
        full = os.path.join(st.preview_dir(), clip["preview"])
        if not os.path.isfile(full):
            return self._error("preview missing", 404)
        self._send_file(full, allow_range=True)

    def _serve_media(self, qs) -> None:
        st = self.state
        path = (qs.get("path") or [None])[0]
        if not path:
            return self._error("path required")
        path = os.path.abspath(path)
        if not st.is_within_library(path) or not os.path.isfile(path):
            return self._error("forbidden", 403)
        self._send_file(path, allow_range=True)

    def _send_file(self, full: str, allow_range: bool = False) -> None:
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        size = os.path.getsize(full)
        rng = self.headers.get("Range") if allow_range else None
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            start = int(m.group(1)) if m and m.group(1) else 0
            end = int(m.group(2)) if m and m.group(2) else size - 1
            end = min(end, size - 1)
            start = min(start, end)
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with open(full, "rb") as fh:
                fh.seek(start)
                self._stream(fh, length)
        else:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            if allow_range:
                self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with open(full, "rb") as fh:
                self._stream(fh, size)

    def _stream(self, fh, length: int) -> None:
        remaining = length
        while remaining > 0:
            chunk = fh.read(min(65536, remaining))
            if not chunk:
                break
            try:
                self.wfile.write(chunk)
            except (ConnectionError, OSError):
                break
            remaining -= len(chunk)


def _params_from(body: dict) -> DetectParams:
    base = DetectParams()
    return DetectParams(
        bits=base.bits,
        max_bit_err=int(body.get("max_bit_err", base.max_bit_err)),
        min_seconds=float(body.get("min_seconds", base.min_seconds)),
        min_shows=int(body.get("min_shows", base.min_shows)),
        max_gap_seconds=base.max_gap_seconds,
        min_density=base.min_density,
        top_offsets=base.top_offsets,
        locate_min_ratio=base.locate_min_ratio,
        dedupe_ratio=base.dedupe_ratio,
    )


def _max_end(segs) -> float:
    return max((e for _, e in segs), default=0.0) + 1.0


def serve(library: str | None, host: str = "127.0.0.1", port: int = 7654,
          workers: int = 1) -> None:
    state = AppState(workers=workers)
    if library:
        try:
            state.open_library(library)
        except FileNotFoundError as exc:
            print(f"  ! {exc}")

    handler = type("BoundHandler", (Handler,), {"state": state})
    httpd = ThreadingHTTPServer((host, port), handler)

    tstat = state.tools.status()
    print("  Fluffless — remove the fluff")
    print(f"  → http://{host}:{port}")
    print(f"  engines: ffmpeg {'ok' if tstat['ffmpeg'] else 'MISSING'} · "
          f"fpcalc {'ok' if tstat['fpcalc'] else 'MISSING'}")
    print(f"  workers: {state.workers}")
    if state.library:
        print(f"  library: {state.library}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
    finally:
        httpd.server_close()
        if state.db:
            state.db.close()

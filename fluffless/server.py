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
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .binaries import detect_tools
from .clips import OUT_DIR, extract_preview, remove_segments
from .db import LABELS, Database
from .media import MediaFile, scan_library
from .repetition import DetectParams
from .scan import scan_folder

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
PREVIEW_DIR = "previews"


class AppState:
    """Server-wide state: the active library, its database, and a folder cache."""

    def __init__(self) -> None:
        self.tools = detect_tools()
        self.library: str | None = None
        self.db: Database | None = None
        self.folders: list = []
        self.lock = threading.Lock()

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
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/library":
                return self._api_open_library()
            if path == "/api/scan":
                return self._api_scan()
            if path == "/api/label":
                return self._api_label()
            if path == "/api/remove":
                return self._api_remove()
            if path == "/api/preview":
                return self._api_make_preview()
            return self._error("not found", 404)
        except Exception as exc:  # noqa: BLE001
            self._error(f"{type(exc).__name__}: {exc}", 500)

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
            "labels": list(LABELS),
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

    # --- API: scan -----------------------------------------------------------

    def _api_scan(self) -> None:
        st = self.state
        if not st.db or not st.library:
            return self._error("open a library first")
        body = self._body()
        folder_name = body.get("folder")
        folder = st.folder(folder_name)
        if not folder:
            return self._error(f"unknown folder: {folder_name}", 404)

        chosen = body.get("files")  # optional list of file paths for a partial scan
        files = folder.files
        if chosen:
            wanted = set(chosen)
            files = [f for f in folder.files if f.path in wanted]
        if not files:
            return self._error("no files selected")

        params = _params_from(body)

        def make_preview(mf: MediaFile, start: float, end: float):
            if not st.tools.has_ffmpeg:
                return None
            try:
                out = extract_preview(mf.path, start, end, st.preview_dir(), st.tools, mf.kind)
                return os.path.relpath(out, st.preview_dir())
            except Exception:
                return None

        with st.lock:
            result = scan_folder(
                st.db, st.library, folder.name, files, st.tools,
                params=params, make_preview=make_preview,
            )
        self._json({
            "folder": folder.name,
            "files_scanned": result.files_scanned,
            "new_patterns": result.new_patterns,
            "matched_patterns": result.matched_patterns,
            "clips_added": result.clips_added,
            "patterns": self._patterns_payload(folder.name),
        })

    # --- API: patterns & labels ----------------------------------------------

    def _api_patterns(self, qs) -> None:
        if not self.state.db:
            return self._error("open a library first")
        folder = (qs.get("folder") or [None])[0]
        self._json({"patterns": self._patterns_payload(folder)})

    def _patterns_payload(self, folder: str | None) -> list[dict]:
        db = self.state.db
        assert db
        out = []
        for row in db.patterns(folder):
            clips = [
                {
                    "id": c["id"],
                    "file_name": c["file_name"],
                    "file_path": c["file_path"],
                    "start": round(c["start"], 2),
                    "end": round(c["end"], 2),
                    "has_preview": bool(c["preview"]),
                }
                for c in db.clips(row["id"])
            ]
            out.append({
                "id": row["id"],
                "folder": row["folder"],
                "label": row["label"],
                "duration": round(row["duration"], 2),
                "shows": row["shows"],
                "bits": row["bits"],
                "clips": clips,
            })
        return out

    def _api_label(self) -> None:
        if not self.state.db:
            return self._error("open a library first")
        body = self._body()
        pid = body.get("pattern_id")
        label = body.get("label")
        if label not in LABELS:
            return self._error(f"label must be one of {LABELS}")
        self.state.db.set_label(int(pid), label)
        self._json({"ok": True})

    # --- API: remove the fluff -----------------------------------------------

    def _api_remove(self) -> None:
        st = self.state
        if not st.db or not st.library:
            return self._error("open a library first")
        st.tools.require("ffmpeg")
        body = self._body()
        folder = st.folder(body.get("folder"))
        if not folder:
            return self._error("unknown folder", 404)
        labels = body.get("labels") or ["Ad"]
        chosen = set(body.get("files") or [])

        # Gather removal segments per file from clips of matching-label patterns.
        per_file: dict[str, list[tuple[float, float, str]]] = {}
        for row in st.db.patterns(folder.name):
            if row["label"] not in labels:
                continue
            for c in st.db.clips(row["id"]):
                if chosen and c["file_path"] not in chosen:
                    continue
                per_file.setdefault(c["file_path"], []).append(
                    (c["start"], c["end"], row["label"])
                )

        out_dir = os.path.join(folder.path, OUT_DIR)
        results = []
        file_by_path = {f.path: f for f in folder.files}
        with st.lock:
            for fpath, segs in per_file.items():
                mf = file_by_path.get(fpath)
                duration = mf.duration if mf else _max_end(segs)
                kind = mf.kind if mf else None
                ranges = [(s, e) for s, e, _ in segs]
                try:
                    out = remove_segments(fpath, ranges, duration, out_dir, st.tools, kind)
                except Exception as exc:  # noqa: BLE001
                    results.append({"file": os.path.basename(fpath), "error": str(exc)})
                    continue
                saved = sum(e - s for s, e in ranges)
                removed = [{"start": round(s, 2), "end": round(e, 2), "label": lbl}
                           for s, e, lbl in segs]
                st.db.add_processed(fpath, out, removed, saved)
                results.append({
                    "file": os.path.basename(fpath),
                    "output": os.path.relpath(out, folder.path),
                    "saved_sec": round(saved, 2),
                    "segments": len(segs),
                })
        self._json({"results": results, "out_dir": os.path.relpath(out_dir, st.library)})

    def _api_make_preview(self) -> None:
        """(Re)build a preview clip on demand for a stored clip."""
        st = self.state
        if not st.db:
            return self._error("open a library first")
        st.tools.require("ffmpeg")
        body = self._body()
        clip = st.db.clip(int(body.get("clip_id")))
        if not clip:
            return self._error("unknown clip", 404)
        out = extract_preview(
            clip["file_path"], clip["start"], clip["end"], st.preview_dir(), st.tools,
        )
        rel = os.path.relpath(out, st.preview_dir())
        st.db.set_clip_preview(clip["id"], rel)
        self._json({"ok": True, "clip_id": clip["id"]})

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
            except (BrokenPipeError, ConnectionResetError):
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
    return max((e for _, e, _ in segs), default=0.0) + 1.0


def serve(library: str | None, host: str = "127.0.0.1", port: int = 7654) -> None:
    state = AppState()
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

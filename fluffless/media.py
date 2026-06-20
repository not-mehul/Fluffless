"""Scan a library root into media folders and classify their contents.

A *library* is a root directory. Inside it, each subfolder that holds media
files is a *media folder* (named for its show / series). Files sitting loose in
the root are grouped under a synthetic "(root)" folder so nothing is lost.

A folder is classified ``video`` if any of its files carry a video stream,
otherwise ``audio`` — driving the audio/video logo in the UI.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .binaries import Tools, run

AUDIO_EXT = {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma", ".m4b"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".wmv", ".ts", ".mpg", ".mpeg"}
MEDIA_EXT = AUDIO_EXT | VIDEO_EXT

ROOT_FOLDER = "(root)"


@dataclass
class MediaFile:
    path: str           # absolute path
    name: str           # basename
    kind: str           # 'audio' | 'video'
    size: int = 0
    duration: float = 0.0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "kind": self.kind,
            "size": self.size,
            "duration": round(self.duration, 2),
        }


@dataclass
class MediaFolder:
    name: str
    path: str
    files: list[MediaFile] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return "video" if any(f.kind == "video" for f in self.files) else "audio"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "count": len(self.files),
            "files": [f.to_dict() for f in self.files],
        }


def classify_ext(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return None


def probe(path: str, tools: Tools) -> tuple[str, float]:
    """Return (kind, duration_seconds) using ffprobe when available.

    Falls back to the file extension if ffprobe is missing or fails, so the
    scanner still works (without exact durations) on a bare system.
    """
    ext_kind = classify_ext(path) or "audio"
    if not tools.has_ffmpeg:
        return ext_kind, 0.0
    try:
        out = run([
            tools.ffprobe, "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", path,
        ]).stdout
        info = json.loads(out)
    except Exception:
        return ext_kind, 0.0
    has_video = any(
        s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic", 0) == 0
        for s in info.get("streams", [])
    )
    kind = "video" if has_video else "audio"
    duration = 0.0
    try:
        duration = float(info.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    return kind, duration


def scan_library(root: str, tools: Tools) -> list[MediaFolder]:
    """Walk ``root`` one level deep into media folders.

    Returns folders sorted by name, each with its probed media files. Hidden
    folders and Fluffless's own ``.fluffless`` workspace are skipped.
    """
    root = os.path.abspath(os.path.expanduser(root))
    folders: dict[str, MediaFolder] = {}

    def add(folder_name: str, folder_path: str, fpath: str) -> None:
        ext_kind = classify_ext(fpath)
        if ext_kind is None:
            return
        kind, duration = probe(fpath, tools)
        mf = MediaFile(
            path=fpath,
            name=os.path.basename(fpath),
            kind=kind,
            size=_safe_size(fpath),
            duration=duration,
        )
        folders.setdefault(folder_name, MediaFolder(name=folder_name, path=folder_path)).files.append(mf)

    # Loose files directly in root.
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    for entry in entries:
        if entry.startswith(".") or entry == "_fluffless_out":
            continue
        full = os.path.join(root, entry)
        if os.path.isfile(full):
            add(ROOT_FOLDER, root, full)
        elif os.path.isdir(full):
            for dirpath, dirnames, filenames in os.walk(full):
                dirnames[:] = [
                    d for d in dirnames
                    if not d.startswith(".") and d not in {"_fluffless_out", "_fluffless_previews"}
                ]
                for fn in sorted(filenames):
                    add(entry, full, os.path.join(dirpath, fn))

    result = [f for f in folders.values() if f.files]
    result.sort(key=lambda f: f.name.lower())
    for f in result:
        f.files.sort(key=lambda x: x.name.lower())
    return result


def _safe_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0

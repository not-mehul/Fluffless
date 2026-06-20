"""Orchestrate a scan over a media folder.

This is the conductor between the detection engine (repetition.py), the
fingerprinters (fingerprint.py), and storage (db.py). For a chosen folder and a
chosen set of files it:

  1. fingerprints each file,
  2. matches every *known* stored pattern against each file (so a single new
     episode gets trimmed without needing peers — Pattern_Detection §7),
  3. runs cross-file recurrence over the batch to discover *new* repeated
     segments,
  4. de-duplicates against existing patterns (a re-found intro bumps one
     pattern's count rather than spawning a copy), and
  5. records every occurrence as a clip with timestamps for in-tool playback.

Progress is reported through a callback so the server can stream it to the UI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

from .binaries import Tools
from .db import Database
from .fingerprint import fingerprint_file
from .media import MediaFile
from .repetition import (
    DetectParams,
    Fingerprint,
    best_ratio,
    locate,
    recurring_segments,
)

ProgressFn = Callable[[dict], None]


@dataclass
class ScanResult:
    folder: str
    files_scanned: int = 0
    new_patterns: list[int] = field(default_factory=list)
    matched_patterns: list[int] = field(default_factory=list)
    clips_added: int = 0


def _emit(progress: ProgressFn | None, **payload) -> None:
    if progress:
        progress(payload)


def scan_folder(
    db: Database,
    library: str,
    folder: str,
    files: list[MediaFile],
    tools: Tools,
    params: DetectParams | None = None,
    progress: ProgressFn | None = None,
    make_preview: Callable[[MediaFile, float, float], str | None] | None = None,
) -> ScanResult:
    base = params or DetectParams()
    result = ScanResult(folder=folder)

    # 1. Fingerprint every chosen file.
    prints: list[tuple[MediaFile, Fingerprint]] = []
    for i, mf in enumerate(files):
        _emit(progress, stage="fingerprint", file=mf.name, index=i, total=len(files))
        try:
            fp = fingerprint_file(mf.path, mf.kind, tools)
        except Exception as exc:  # noqa: BLE001 — surface, don't abort the batch
            _emit(progress, stage="error", file=mf.name, message=str(exc))
            continue
        prints.append((mf, fp))
    result.files_scanned = len(prints)

    # 2. Match known patterns against each file (works even for a single file).
    known = db.patterns(folder)
    for mf, fp in prints:
        p = base.scaled(fp.bits)
        for row in known:
            if row["bits"] != fp.bits:
                continue
            items = db.pattern_items(row)
            hit = locate(items, fp.items, p)
            if hit is None:
                continue
            start = hit[0] * fp.item_sec
            end = hit[1] * fp.item_sec
            if db.clip_exists(row["id"], mf.path, start):
                continue
            preview = make_preview(mf, start, end) if make_preview else None
            db.add_clip(row["id"], mf.path, start, end, preview)
            db.bump_pattern(row["id"])
            result.clips_added += 1
            if row["id"] not in result.matched_patterns:
                result.matched_patterns.append(row["id"])
            _emit(progress, stage="matched", file=mf.name, pattern_id=row["id"],
                  label=row["label"], start=start, end=end)

    # 3. Cross-file recurrence over the batch (group by bit-width).
    by_bits: dict[int, list[tuple[MediaFile, Fingerprint]]] = {}
    for mf, fp in prints:
        by_bits.setdefault(fp.bits, []).append((mf, fp))

    for bits, group in by_bits.items():
        if len(group) < base.min_shows:
            continue  # need at least min_shows files to recur against
        p = base.scaled(bits)
        _emit(progress, stage="detect", count=len(group))
        segs_per_file = recurring_segments([fp for _, fp in group], p)
        for (mf, fp), segs in zip(group, segs_per_file):  # noqa: B905
            for start, end in segs:
                pid = _store_segment(db, library, folder, mf, fp, start, end, p, make_preview)
                if pid is None:
                    continue
                if pid not in result.new_patterns and pid not in result.matched_patterns:
                    result.new_patterns.append(pid)
                result.clips_added += 1
                _emit(progress, stage="found", file=mf.name, pattern_id=pid,
                      start=start, end=end)

    _emit(progress, stage="done", **{
        "files": result.files_scanned,
        "new": len(result.new_patterns),
        "matched": len(result.matched_patterns),
        "clips": result.clips_added,
    })
    return result


def _store_segment(
    db: Database, library: str, folder: str, mf: MediaFile, fp: Fingerprint,
    start: float, end: float, p: DetectParams,
    make_preview: Callable[[MediaFile, float, float], str | None] | None,
) -> int | None:
    """Store a detected segment, de-duplicating against existing patterns.

    If the segment matches a stored pattern (ratio > dedupe_ratio) we attach a
    clip to that pattern and bump its count; otherwise we mint a new pattern.
    Returns the pattern id, or None if this exact occurrence was already known.
    """
    items = fp.slice_seconds(start, end)
    if not items:
        return None

    match_id = None
    for row in db.patterns(folder):
        if row["bits"] != fp.bits:
            continue
        if best_ratio(items, db.pattern_items(row), p) >= p.dedupe_ratio:
            match_id = row["id"]
            break

    if match_id is None:
        match_id = db.add_pattern(
            library, folder, items, fp.item_sec, fp.bits, end - start, label="Other",
        )
    else:
        if db.clip_exists(match_id, mf.path, start):
            return None
        db.bump_pattern(match_id)

    if db.clip_exists(match_id, mf.path, start):
        return match_id
    preview = make_preview(mf, start, end) if make_preview else None
    db.add_clip(match_id, mf.path, start, end, preview)
    return match_id

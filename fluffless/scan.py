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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    locate_all,
    recurring_segments,
)

ProgressFn = Callable[[dict], None]

# Two occurrences belong to the same pattern only if they are the *same audio* —
# which means a similar length. Without this, a short ad whose fingerprint is
# contained in a longer "two ads back-to-back" segment gets merged into it, so a
# single pattern ends up mixing 74s and 143s clips.
LENGTH_TOL = 0.20


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


def _fingerprint_all(
    files: list[MediaFile], tools: Tools, progress: ProgressFn | None, workers: int,
) -> list[tuple[MediaFile, Fingerprint]]:
    """Fingerprint every file, in parallel when asked. Results keep the input
    order; failures are surfaced per file and skipped, not fatal."""
    total = len(files)
    slots: list[tuple[MediaFile, Fingerprint] | None] = [None] * total

    if workers and workers > 1 and total > 1:
        done = 0
        with ThreadPoolExecutor(max_workers=min(workers, total)) as ex:
            futures = {
                ex.submit(fingerprint_file, mf.path, mf.kind, tools): (idx, mf)
                for idx, mf in enumerate(files)
            }
            for fut in as_completed(futures):
                idx, mf = futures[fut]
                _emit(progress, stage="fingerprint", file=mf.name, index=done, total=total)
                done += 1
                try:
                    slots[idx] = (mf, fut.result())
                except Exception as exc:  # noqa: BLE001 — surface, don't abort the batch
                    _emit(progress, stage="error", file=mf.name, message=str(exc))
    else:
        for idx, mf in enumerate(files):
            _emit(progress, stage="fingerprint", file=mf.name, index=idx, total=total)
            try:
                slots[idx] = (mf, fingerprint_file(mf.path, mf.kind, tools))
            except Exception as exc:  # noqa: BLE001
                _emit(progress, stage="error", file=mf.name, message=str(exc))

    return [s for s in slots if s is not None]


def scan_folder(
    db: Database,
    library: str,
    folder: str,
    files: list[MediaFile],
    tools: Tools,
    params: DetectParams | None = None,
    progress: ProgressFn | None = None,
    make_preview: Callable[[MediaFile, float, float], str | None] | None = None,
    workers: int = 1,
) -> ScanResult:
    base = params or DetectParams()
    result = ScanResult(folder=folder)

    # 1. Fingerprint every chosen file (parallel: fpcalc/ffmpeg are subprocesses,
    #    so threads run several at once across cores).
    prints = _fingerprint_all(files, tools, progress, workers)
    # Cache fingerprints so a later-identified ad can be located in these files
    # without re-reading the audio.
    for mf, fp in prints:
        db.store_fingerprint(mf.path, folder, fp)
    result.files_scanned = len(prints)

    # 2. Match *confirmed* signatures against each file (works for a single
    #    file). Only segments the user has confirmed as ads are reused as known
    #    signatures — pending/dismissed ones aren't approved, and forcing them
    #    onto new files would pre-empt the user's review. ``locate_all`` catches
    #    every airing in a file, so an ad shown twice gets two clips.
    known = [row for row in db.patterns(folder) if row["status"] == "confirmed"]
    for mf, fp in prints:
        p = base.scaled(fp.bits)
        for row in known:
            if row["bits"] != fp.bits:
                continue
            if row["duration"] < base.min_seconds:
                continue  # honour the minimum-length filter for stored patterns too
            items = db.pattern_items(row)
            for s_item, e_item in locate_all(items, fp.items, p):
                start = s_item * fp.item_sec
                end = e_item * fp.item_sec
                if end - start < 0.2 or db.clip_exists(row["id"], mf.path, start):
                    continue
                preview = make_preview(mf, start, end) if make_preview else None
                db.add_clip(row["id"], mf.path, start, end, preview)
                db.bump_pattern(row["id"])
                result.clips_added += 1
                if row["id"] not in result.matched_patterns:
                    result.matched_patterns.append(row["id"])
                _emit(progress, stage="matched", file=mf.name, pattern_id=row["id"],
                      start=start, end=end)

    # 3. Cross-file recurrence over the batch (group by bit-width).
    by_bits: dict[int, list[tuple[MediaFile, Fingerprint]]] = {}
    for mf, fp in prints:
        by_bits.setdefault(fp.bits, []).append((mf, fp))

    for bits, group in by_bits.items():
        if len(group) < base.min_shows:
            continue  # need at least min_shows files to recur against
        p = base.scaled(bits)
        _emit(progress, stage="detect", count=len(group))

        def _det_progress(done: int, total: int) -> None:
            _emit(progress, stage="detect_progress", done=done, total=total)

        segs_per_file = recurring_segments(
            [fp for _, fp in group], p, on_progress=_det_progress, workers=workers,
        )
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

    # Normalise lengths: re-locate every new pattern's clips against its
    # canonical fingerprint, so the *same ad* gets the *same length* in every
    # file (the cross-file recurrence boundaries vary; the fingerprint does not).
    if result.new_patterns:
        fp_by_path = {mf.path: fp for mf, fp in prints}
        _emit(progress, stage="normalize", count=len(result.new_patterns))
        _normalize_pattern_lengths(db, result.new_patterns, fp_by_path, base)

    _emit(progress, stage="done", **{
        "files": result.files_scanned,
        "new": len(result.new_patterns),
        "matched": len(result.matched_patterns),
        "clips": result.clips_added,
    })
    return result


def relocate_group_from_clip(
    db: Database, clip_id: int, base: DetectParams | None = None,
    split_nonmatches: bool = True,
) -> dict | None:
    """Re-derive a whole group from one hand-cropped clip.

    This is the "I found the ad — now find it everywhere" step. It pins the
    clip's current (cropped) region as its pattern's canonical fingerprint, then
    locates *that exact segment* in every cached file of the folder:

      * occurrences that line up with a clip already in the group are snapped to
        the cropped length, so every member is the same exact segment;
      * files that contain the segment but weren't in the group are pulled in;
      * if ``split_nonmatches`` is True: clips that no longer contain the segment
        are moved out into a new group — they're a different ad to review.
        When False (clip was not cropped), existing clips that can't be re-located
        are left in place rather than evicted; only the snap and pull-in steps run.

    The result: one tight group of identical-length occurrences the user can
    confirm together. Returns a summary dict (or ``{"error": ...}`` / ``None``).
    """
    base = base or DetectParams()
    clip = db.clip(clip_id)
    if clip is None:
        return None
    prow = db.pattern(clip["pattern_id"])
    if prow is None:
        return None
    pid = prow["id"]

    src_fp = db.get_fingerprint(clip["file_path"])
    if src_fp is None:
        return {"error": "no cached fingerprint for this file — re-scan the folder first"}
    items = list(src_fp.slice_seconds(clip["start"], clip["end"]))
    if len(items) < 1:
        return {"error": "selection is too short to fingerprint"}
    crop_dur = max(src_fp.item_sec, clip["end"] - clip["start"])

    # Lock the cropped region in as the group's fingerprint (future scans + a
    # later "mark as ad" both reuse exactly this span).
    db.pin_fingerprint(pid, items, crop_dur)

    p = base.scaled(prow["bits"])

    # 1. Locate the cropped segment everywhere in the folder. Every located
    #    occurrence gets the *same* length (crop_dur) so the group is uniform.
    targets: dict[str, list[float]] = {}        # file_path -> located start times
    for file_path, fp in db.fingerprints(prow["folder"]):
        if fp.bits != prow["bits"]:
            continue
        for s_item, _e_item in locate_all(items, fp.items, p):
            targets.setdefault(file_path, []).append(s_item * fp.item_sec)

    # 2. Reconcile the group's existing clips against the located occurrences.
    #    A clip overlapping an occurrence in its file is the *same* airing →
    #    snap it; a clip with no overlapping occurrence is a different segment.
    consumed: set[tuple[str, int]] = set()
    snapped = 0
    nonmatch_ids: list[int] = []
    for c in db.clips(pid):
        starts = targets.get(c["file_path"], [])
        hit = None
        for i, ts in enumerate(starts):
            if (c["file_path"], i) in consumed:
                continue
            te = ts + crop_dur
            if min(c["end"], te) - max(c["start"], ts) > 0:    # any overlap
                hit = (i, ts, te)
                break
        if hit is None:
            if split_nonmatches:
                nonmatch_ids.append(c["id"])
            continue
        i, ts, te = hit
        consumed.add((c["file_path"], i))
        db.set_clip_detected(c["id"], ts, te)
        snapped += 1

    # 3. Add the occurrences nothing claimed — episodes (or repeat airings) that
    #    contain the segment but weren't in the group yet.
    added = 0
    for file_path, starts in targets.items():
        for i, ts in enumerate(starts):
            if (file_path, i) in consumed:
                continue
            if db.clip_exists(pid, file_path, ts):
                continue
            db.add_clip(pid, file_path, ts, ts + crop_dur)
            added += 1

    # 4. Move the non-matching clips out into their own pending group.
    leftover_id = None
    if nonmatch_ids:
        res = db.new_group_from_clip(nonmatch_ids[0], status="pending")
        if res and "error" not in res:
            leftover_id = res["new_pattern_id"]
            for cid in nonmatch_ids[1:]:
                db.move_clip(cid, leftover_id)

    db.recount_shows(pid)
    return {
        "pattern_id": pid,
        "folder": prow["folder"],
        "snapped": snapped,
        "added": added,
        "moved_out": len(nonmatch_ids),
        "leftover_group_id": leftover_id,
        "duration": crop_dur,
    }


def _covered(intervals: list[tuple[float, float]], s: float, e: float, tol: float) -> bool:
    """True when [s, e] has no gap larger than ``tol`` left uncovered by the
    union of ``intervals`` (each a (start, end) pair)."""
    cursor = s
    for cs, ce in sorted(intervals):
        if ce <= cursor:
            continue
        if cs > cursor + tol:          # an uncovered gap wider than tolerance
            return False
        cursor = max(cursor, ce)
        if cursor >= e - tol:
            return True
    return cursor >= e - tol


def dedupe_contained_clips(
    db: Database, folder: str, tol: float = 1.5,
) -> tuple[int, list[int]]:
    """Collapse nested detections to their finest pieces.

    When the same audio is captured at two granularities — e.g. two ads detected
    individually *and* as one back-to-back block — the larger block is redundant.
    This drops any clip whose span is fully covered (no gap wider than ``tol``
    seconds) by the union of strictly-smaller clips **of the same review status**
    in the same file, keeping the atomic pieces. Groups left with no clips are
    deleted, so the group count falls as duplicates are resolved.

    Restricting coverage to the same status preserves removal output: a confirmed
    clip is only ever dropped when smaller *confirmed* clips already cut the same
    seconds. Returns (clips_removed, deleted_pattern_ids).
    """
    status_by_pat = {prow["id"]: prow["status"] for prow in db.patterns(folder)}
    by_key: dict[tuple[str, str], list] = {}
    for c in db.clips():
        status = status_by_pat.get(c["pattern_id"])
        if status is None:                       # clip in another folder
            continue
        by_key.setdefault((c["file_path"], status), []).append(c)

    to_delete: list[int] = []
    affected: set[int] = set()
    for clips in by_key.values():
        clips.sort(key=lambda c: c["end"] - c["start"], reverse=True)  # largest first
        survivors = list(clips)
        for c in clips:
            cdur = c["end"] - c["start"]
            smaller = [
                (o["start"], o["end"]) for o in survivors
                if o["id"] != c["id"] and (o["end"] - o["start"]) < cdur - 1e-6
            ]
            if _covered(smaller, c["start"], c["end"], tol):
                to_delete.append(c["id"])
                affected.add(c["pattern_id"])
                survivors = [o for o in survivors if o["id"] != c["id"]]

    for cid in to_delete:
        db.delete_clip(cid)
    deleted_patterns = [pid for pid in affected if db.recount_shows(pid) == 0]
    return len(to_delete), deleted_patterns


def absorb_overlapping_pending(
    db: Database,
    confirmed_pattern_id: int,
    overlap_threshold: float = 0.80,
) -> list[int]:
    """Dismiss pending patterns whose clips are substantially covered by a
    newly-confirmed pattern's clips.

    After confirming a pattern and back-applying it, pending patterns that were
    detecting the same segment — perhaps with slightly different boundaries —
    are redundant. This auto-dismisses them so the user isn't left with a pile
    of near-duplicate cards to review manually.

    A pending clip counts as "covered" when the confirmed pattern has a clip in
    the same file whose time-overlap with the pending clip reaches at least
    ``overlap_threshold`` of the pending clip's duration.  A pending pattern is
    absorbed when at least ``overlap_threshold`` of its clips are covered this
    way.  Only ``pending`` patterns are affected.  Returns absorbed pattern ids.
    """
    confirmed_row = db.pattern(confirmed_pattern_id)
    if confirmed_row is None:
        return []

    confirmed_by_file: dict[str, list[tuple[float, float]]] = {}
    for c in db.clips(confirmed_pattern_id):
        confirmed_by_file.setdefault(c["file_path"], []).append((c["start"], c["end"]))
    if not confirmed_by_file:
        return []

    absorbed: list[int] = []
    for row in db.patterns(confirmed_row["folder"]):
        if row["id"] == confirmed_pattern_id or row["status"] != "pending":
            continue
        pending_clips = db.clips(row["id"])
        if not pending_clips:
            continue
        covered = 0
        for pc in pending_clips:
            ps, pe = pc["start"], pc["end"]
            pending_dur = pe - ps
            if pending_dur <= 0:
                continue
            for cs, ce in confirmed_by_file.get(pc["file_path"], []):
                if max(0.0, min(pe, ce) - max(ps, cs)) / pending_dur >= overlap_threshold:
                    covered += 1
                    break
        if covered / len(pending_clips) >= overlap_threshold:
            db.set_status(row["id"], "dismissed")
            absorbed.append(row["id"])
    return absorbed


def apply_pattern_to_stored(
    db: Database, pattern_id: int, base: DetectParams | None = None,
) -> int:
    """Locate every occurrence of one pattern's fingerprint in every cached file
    of its folder and add a clip wherever it occurs but isn't already recorded.
    This is the "re-parse all media for this confirmed segment" step: confirming
    an ad back-applies it to files scanned before it was known *and* catches any
    repeat airings within a file (``locate_all``) — no audio is re-read. Returns
    the number of clips added."""
    base = base or DetectParams()
    row = db.pattern(pattern_id)
    if row is None:
        return 0
    items = db.pattern_items(row)
    if not items:
        return 0
    p = base.scaled(row["bits"])
    added = 0
    for file_path, fp in db.fingerprints(row["folder"]):
        if fp.bits != row["bits"]:
            continue
        for s_item, e_item in locate_all(items, fp.items, p):
            start = s_item * fp.item_sec
            end = e_item * fp.item_sec
            if end - start < 0.2 or db.clip_exists(pattern_id, file_path, start):
                continue
            db.add_clip(pattern_id, file_path, start, end)
            db.bump_pattern(pattern_id)
            added += 1
    return added


def _normalize_pattern_lengths(
    db: Database, pattern_ids: list[int],
    fp_by_path: dict[str, Fingerprint], base: DetectParams,
) -> None:
    """Make a pattern's clips a consistent length. Picks a median-length clip as
    the canonical occurrence, re-locates that fingerprint in every member file,
    and snaps each clip to the located span (``locate`` naturally trims silence /
    low-entropy over-capture). The pattern's stored fingerprint and duration are
    then re-derived from a median *located* span, so the pattern's length stays
    in step with its clips. A clip whose match is implausibly off-length is left
    as-is."""
    for pid in pattern_ids:
        prow = db.pattern(pid)
        if prow is not None and "pinned" in prow.keys() and prow["pinned"]:
            continue  # respect a hand-chosen fingerprint — never re-normalise it
        clips = db.clips(pid)
        avail = [(c, fp_by_path[c["file_path"]]) for c in clips if c["file_path"] in fp_by_path]
        if len(avail) < 2:
            continue
        avail.sort(key=lambda ce: ce[0]["end"] - ce[0]["start"])
        cclip, cfp = avail[len(avail) // 2]          # median-length occurrence
        p = base.scaled(cfp.bits)
        canon = cfp.slice_seconds(cclip["start"], cclip["end"])
        if not canon:
            continue
        canon_dur = cclip["end"] - cclip["start"]

        relocated: list[tuple] = []
        for c, fp in avail:
            hit = locate(canon, fp.items, p)
            if not hit:
                continue
            s = hit[0] * fp.item_sec
            e = hit[1] * fp.item_sec
            if e > s and abs((e - s) - canon_dur) <= 0.35 * canon_dur + 1.0:
                relocated.append((c, fp, s, e))
        if not relocated:
            continue

        # Re-derive the pattern fingerprint from a median *located* occurrence so
        # its stored duration matches the (tightened) clips.
        relocated.sort(key=lambda r: r[3] - r[2])
        _, mfp, ms, me = relocated[len(relocated) // 2]
        tight = mfp.slice_seconds(ms, me)
        if tight:
            db.set_pattern_fingerprint(pid, tight, me - ms)
        for c, _fp, s, e in relocated:
            db.set_clip_detected(c["id"], s, e)


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
    seg_dur = end - start

    match_id = None
    for row in db.patterns(folder):
        if row["bits"] != fp.bits:
            continue
        # Same audio ⇒ similar length. Skip patterns whose duration differs too
        # much, so a short ad isn't absorbed into a longer combined segment.
        pdur = row["duration"]
        longer = max(seg_dur, pdur)
        if longer > 0 and abs(seg_dur - pdur) / longer > LENGTH_TOL:
            continue
        if best_ratio(items, db.pattern_items(row), p) >= p.dedupe_ratio:
            match_id = row["id"]
            break

    if match_id is None:
        match_id = db.add_pattern(
            library, folder, items, fp.item_sec, fp.bits, end - start, status="pending",
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

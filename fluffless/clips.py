"""Extract preview clips and trim segments out of media — both via ffmpeg.

* :func:`extract_preview` cuts a short, web-playable copy of a detected segment
  so the user can preview a duplicate as many times as needed before cataloging.
* :func:`remove_segments` writes a new file with the chosen segments cut out and
  the surrounding content concatenated — "Remove the Fluff". The original is
  never touched; output lands in a sibling ``_fluffless_out`` folder.
"""

from __future__ import annotations

import os

from .binaries import Tools, run
from .media import classify_ext

OUT_DIR = "_fluffless_out"


def _preview_ext(src: str, kind: str) -> str:
    return ".mp4" if kind == "video" else ".m4a"


def extract_preview(
    src: str, start: float, end: float, dest_dir: str, tools: Tools, kind: str | None = None,
) -> str:
    """Cut [start, end) from ``src`` into ``dest_dir`` as a re-encoded preview.

    Re-encoding (rather than stream-copy) keeps the short clip seekable and
    keyframe-clean for in-browser playback. Returns the output path.
    """
    tools.require("ffmpeg")
    os.makedirs(dest_dir, exist_ok=True)
    kind = kind or (classify_ext(src) or "audio")
    base = os.path.splitext(os.path.basename(src))[0]
    out = os.path.join(dest_dir, f"{base}_{int(start*1000)}_{int(end*1000)}{_preview_ext(src, kind)}")
    dur = max(0.2, end - start)
    if kind == "video":
        cmd = [
            tools.ffmpeg, "-v", "error", "-y",
            "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-c:a", "aac", "-movflags", "+faststart", out,
        ]
    else:
        cmd = [
            tools.ffmpeg, "-v", "error", "-y",
            "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}",
            "-c:a", "aac", "-b:a", "128k", out,
        ]
    run(cmd)
    return out


def _complement(segments: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """The keep-ranges: everything *outside* the segments to remove."""
    if duration <= 0:
        return []
    segs = sorted((max(0.0, s), min(duration, e)) for s, e in segments if e > s)
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in segs:
        if s > cursor:
            keep.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        keep.append((cursor, duration))
    # Drop slivers shorter than a frame's worth.
    return [(s, e) for s, e in keep if e - s > 0.05]


def remove_segments(
    src: str, segments: list[tuple[float, float]], duration: float,
    out_dir: str, tools: Tools, kind: str | None = None,
) -> str:
    """Write a copy of ``src`` with ``segments`` removed and the rest joined.

    Uses ffmpeg's trim/concat filter graph so cuts are frame-accurate and a
    single output is produced in one pass. Returns the output path.
    """
    tools.require("ffmpeg")
    os.makedirs(out_dir, exist_ok=True)
    kind = kind or (classify_ext(src) or "audio")
    keep = _complement(segments, duration)
    out = os.path.join(out_dir, os.path.basename(src))

    if not segments:
        # Nothing to remove — still produce an output copy for a uniform result.
        run([tools.ffmpeg, "-v", "error", "-y", "-i", src, "-c", "copy", out])
        return out
    if not keep:
        raise ValueError("removing the requested segments would leave nothing")

    has_video = kind == "video"
    parts = []
    concat_inputs = []
    n = len(keep)
    for idx, (s, e) in enumerate(keep):
        if has_video:
            parts.append(
                f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{idx}];"
                f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{idx}]"
            )
            concat_inputs.append(f"[v{idx}][a{idx}]")
        else:
            parts.append(
                f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{idx}]"
            )
            concat_inputs.append(f"[a{idx}]")

    if has_video:
        graph = ";".join(parts) + ";" + "".join(concat_inputs) + f"concat=n={n}:v=1:a=1[outv][outa]"
        cmd = [
            tools.ffmpeg, "-v", "error", "-y", "-i", src,
            "-filter_complex", graph, "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-movflags", "+faststart", out,
        ]
    else:
        graph = ";".join(parts) + ";" + "".join(concat_inputs) + f"concat=n={n}:v=0:a=1[outa]"
        cmd = [
            tools.ffmpeg, "-v", "error", "-y", "-i", src,
            "-filter_complex", graph, "-map", "[outa]",
            "-c:a", "aac", "-b:a", "192k", out,
        ]
    run(cmd)
    return out

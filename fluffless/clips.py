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


def extract_preview(
    src: str, start: float, end: float, dest_dir: str, tools: Tools, kind: str | None = None,
) -> str:
    """Cut [start, end) from ``src`` into ``dest_dir`` as a web-playable preview.

    Tries progressively more forgiving strategies so a preview is produced
    across the many ffmpeg builds in the wild (a build without ``libx264``,
    for instance, is common on Windows and would otherwise fail outright):

      video → re-encode H.264/AAC → stream-copy → audio-only
      audio → re-encode AAC       → stream-copy

    Returns the path of the first strategy that produces a non-empty file, or
    raises with the underlying ffmpeg error if every strategy fails.
    """
    tools.require("ffmpeg")
    os.makedirs(dest_dir, exist_ok=True)
    kind = kind or (classify_ext(src) or "audio")
    base = os.path.splitext(os.path.basename(src))[0]
    stamp = f"{int(start * 1000)}_{int(end * 1000)}"
    ss = f"{start:.3f}"
    t = f"{max(0.2, end - start):.3f}"
    ff = tools.ffmpeg

    def out_path(ext: str) -> str:
        return os.path.join(dest_dir, f"{base}_{stamp}{ext}")

    head = [ff, "-v", "error", "-y", "-ss", ss, "-i", src, "-t", t]
    src_ext = os.path.splitext(src)[1].lower() or ".mka"
    mp4, m4a = out_path(".mp4"), out_path(".m4a")
    if kind == "video":
        # Map only the first video + first audio so cover-art / extra streams
        # never reach the muxer.
        sel = ["-map", "0:v:0?", "-map", "0:a:0?"]
        strategies = [
            # Best: re-encode — seekable, keyframe-clean, always plays inline.
            (head + sel + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                           "-c:a", "aac", "-movflags", "+faststart", mp4], mp4),
            # No encoder needed — works on a minimal ffmpeg build.
            (head + sel + ["-c", "copy", "-movflags", "+faststart", mp4], mp4),
            # Last resort: at least give an audible preview.
            (head + ["-vn", "-c:a", "aac", "-b:a", "128k", m4a], m4a),
        ]
    else:
        # `-vn` drops embedded cover art (common in MP3/M4A), which otherwise
        # breaks the muxer. The copy fallback keeps the source's own container
        # so an MP3 stream isn't forced into an incompatible .m4a.
        strategies = [
            (head + ["-vn", "-c:a", "aac", "-b:a", "128k", m4a], m4a),
            (head + ["-vn", "-c:a", "copy", out_path(src_ext)], out_path(src_ext)),
        ]

    last_error: Exception | None = None
    for cmd, out in strategies:
        try:
            run(cmd)
        except Exception as exc:  # noqa: BLE001 — try the next strategy
            last_error = exc
            continue
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
    raise RuntimeError(
        f"could not build a preview for {os.path.basename(src)} — {last_error}"
    )


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


def _audio_args(enc: str) -> list[str]:
    if enc in ("flac", "pcm_s16le"):
        return ["-c:a", enc]            # lossless / uncompressed take no bitrate
    return ["-c:a", enc, "-b:a", "192k"]


# Source container → (encoder, output extension) for re-encoded output. The trim
# graph forces a re-encode, and the codec must match the container (AAC cannot
# live in an .mp3, etc.), which is what broke removal for MP3s.
_AUDIO_ENC = {
    ".mp3": ("libmp3lame", ".mp3"),
    ".m4a": ("aac", ".m4a"), ".m4b": ("aac", ".m4a"), ".aac": ("aac", ".m4a"), ".mp4": ("aac", ".m4a"),
    ".opus": ("libopus", ".opus"), ".ogg": ("libvorbis", ".ogg"), ".oga": ("libvorbis", ".ogg"),
    ".flac": ("flac", ".flac"), ".wav": ("pcm_s16le", ".wav"),
}


def remove_segments(
    src: str, segments: list[tuple[float, float]], duration: float,
    out_dir: str, tools: Tools, kind: str | None = None,
) -> str:
    """Write a copy of ``src`` with ``segments`` removed and the rest joined.

    Uses ffmpeg's trim/concat filter graph so cuts are frame-accurate and a
    single output is produced in one pass. Because the filter forces a
    re-encode, the output codec is chosen to fit the container (keeping the
    source format when possible) with a universal AAC fallback. Returns the
    output path (its extension may differ from the source if a fallback ran).
    """
    tools.require("ffmpeg")
    os.makedirs(out_dir, exist_ok=True)
    kind = kind or (classify_ext(src) or "audio")
    keep = _complement(segments, duration)
    base = os.path.splitext(os.path.basename(src))[0]
    src_ext = os.path.splitext(src)[1].lower()

    if not segments:
        # Nothing to remove — still produce an output copy for a uniform result.
        out = os.path.join(out_dir, os.path.basename(src))
        run([tools.ffmpeg, "-v", "error", "-y", "-i", src, "-c", "copy", out])
        return out
    if not keep:
        raise ValueError("removing the requested segments would leave nothing")

    has_video = kind == "video"
    parts: list[str] = []
    concat_inputs: list[str] = []
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

    # Carry the source's tags (title/artist/album/…) onto the trimmed copy.
    meta = ["-map_metadata", "0"]
    if has_video:
        graph = ";".join(parts) + ";" + "".join(concat_inputs) + f"concat=n={n}:v=1:a=1[outv][outa]"
        maps = ["-map", "[outv]", "-map", "[outa]"]
        # Always land on .mp4 (H.264/AAC) — valid for any source container.
        strategies = [
            (maps + meta + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-c:a", "aac", "-movflags", "+faststart"], ".mp4"),
            (maps + meta + ["-c:v", "mpeg4", "-q:v", "4", "-c:a", "aac"], ".mp4"),
        ]
    else:
        graph = ";".join(parts) + ";" + "".join(concat_inputs) + f"concat=n={n}:v=0:a=1[outa]"
        enc, ext = _AUDIO_ENC.get(src_ext, ("aac", ".m4a"))
        # Keep embedded cover art (a still attached-pic stream) by copying it
        # through, alongside the re-stitched audio and the metadata.
        cover = ["-map", "0:v:0?", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
        strategies = [
            (["-map", "[outa]"] + cover + meta + _audio_args(enc), ext),
            (["-map", "[outa]"] + meta + _audio_args(enc), ext),          # drop cover if it choked
            (["-map", "[outa]"] + meta + _audio_args("aac"), ".m4a"),     # universal fallback
        ]

    last_error: Exception | None = None
    for extra, ext in strategies:
        out = os.path.join(out_dir, base + ext)
        cmd = [tools.ffmpeg, "-v", "error", "-y", "-i", src,
               "-filter_complex", graph, *extra, out]
        try:
            run(cmd)
        except Exception as exc:  # noqa: BLE001 — try the next encoder
            last_error = exc
            continue
        if os.path.exists(out) and os.path.getsize(out) > 0:
            return out
    raise RuntimeError(
        f"could not trim {os.path.basename(src)} — {last_error}"
    )

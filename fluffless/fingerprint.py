"""Turn a media file into a fixed-rate integer stream the matcher can consume.

Two instantiations, one matcher:
  * audio → Chromaprint via ``fpcalc`` → 32-bit items (~8/s)
  * video → perceptual frame hashing via ``ffmpeg`` → 64-bit dHash items

Both return a :class:`~fluffless.repetition.Fingerprint` (items + item_sec +
bits); the rest of the pipeline never cares which one produced it.
"""

from __future__ import annotations

from .binaries import Tools, run
from .repetition import Fingerprint

# --- Audio: Chromaprint ------------------------------------------------------

def fingerprint_audio(path: str, tools: Tools) -> Fingerprint:
    tools.require("fpcalc")
    out = run([tools.fpcalc, "-raw", "-length", "100000", path]).stdout
    duration = 0.0
    items: list[int] = []
    for line in out.splitlines():
        if line.startswith("DURATION="):
            duration = float(line.split("=", 1)[1])
        elif line.startswith("FINGERPRINT="):
            raw = line.split("=", 1)[1]
            items = [int(x) & 0xFFFFFFFF for x in raw.split(",") if x]
    if not items:
        raise ValueError(f"fpcalc produced no fingerprint for {path}")
    # Seconds-per-item is measured per file so timestamps stay exact (§2).
    item_sec = duration / len(items) if duration else 0.1238
    return Fingerprint(items=items, item_sec=item_sec, bits=32)


# --- Video: perceptual frame hashing (dHash) ---------------------------------

DEFAULT_FPS = 4          # "item rate" for video — sample a few frames/second
HASH_W, HASH_H = 9, 8    # dHash grid: 9x8 grayscale → 8x8 = 64 comparisons


def _dhash(gray: bytes, width: int, height: int) -> int:
    """Difference hash: 1 bit per horizontal neighbour comparison.

    Visually-similar frames produce hashes with small Hamming distance, which
    is exactly what the matcher consumes (Pattern_Detection §9 Option B).
    """
    h = 0
    bit = 0
    for y in range(height):
        row = y * width
        for x in range(width - 1):
            left = gray[row + x]
            right = gray[row + x + 1]
            if right > left:
                h |= 1 << bit
            bit += 1
    return h & 0xFFFFFFFFFFFFFFFF


def _run_binary(cmd: list[str]) -> bytes:
    """Run a command capturing *binary* stdout (ffmpeg rawvideo)."""
    import subprocess
    return subprocess.run(cmd, check=True, capture_output=True, timeout=600).stdout


def fingerprint_video(path: str, tools: Tools, fps: int = DEFAULT_FPS) -> Fingerprint:
    """Sample frames at ``fps``, grayscale + downscale, dHash each → 64-bit items.

    Normalisation (grayscale, fixed small size) washes out resolution and
    encoding differences so the same content hashes near-identically
    (Pattern_Detection §9 Option B).
    """
    tools.require("ffmpeg")
    cmd = [
        tools.ffmpeg, "-v", "error", "-i", path,
        "-vf", f"fps={fps},scale={HASH_W}:{HASH_H}:flags=area,format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    data = _run_binary(cmd)
    frame_bytes = HASH_W * HASH_H
    items = [
        _dhash(data[off:off + frame_bytes], HASH_W, HASH_H)
        for off in range(0, len(data) - frame_bytes + 1, frame_bytes)
    ]
    if not items:
        raise ValueError(f"ffmpeg produced no frames for {path}")
    return Fingerprint(items=items, item_sec=1.0 / fps, bits=64)


def fingerprint_file(path: str, kind: str, tools: Tools) -> Fingerprint:
    """Dispatch on media kind. ``kind`` is 'audio' or 'video'.

    For video we fingerprint the *audio track* when present (cheapest, most
    robust — §9 Option A); callers that want visual hashing use
    :func:`fingerprint_video_bytes` directly.
    """
    if kind == "audio":
        return fingerprint_audio(path, tools)
    if kind == "video":
        # Option A: reuse the audio track if the file carries one.
        try:
            return fingerprint_audio(path, tools)
        except Exception:
            return fingerprint_video(path, tools)
    raise ValueError(f"unknown media kind: {kind}")

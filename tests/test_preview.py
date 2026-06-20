"""Preview-extraction tests. These need ffmpeg; they self-skip without it."""

import os
import subprocess
import tempfile

import fluffless.clips as clips
from fluffless.binaries import detect_tools


def _make_video(path: str, tools) -> None:
    subprocess.run(
        [tools.ffmpeg, "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=160x120:rate=10:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", path],
        check=True, capture_output=True,
    )


def test_preview_falls_back_when_encoder_missing():
    tools = detect_tools()
    if not tools.has_ffmpeg:
        return  # ffmpeg not installed — nothing to test
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "clip.mp4")
        _make_video(src, tools)
        dest = os.path.join(d, "previews")

        # Normal path produces an .mp4 preview.
        out = clips.extract_preview(src, 0.5, 1.5, dest, tools, "video")
        assert out.endswith(".mp4") and os.path.getsize(out) > 0

        # Simulate an ffmpeg build without libx264: the re-encode strategy fails,
        # so extraction must fall back (stream-copy) and still produce a file.
        orig = clips.run
        def patched(cmd, *a, **k):
            if "libx264" in cmd:
                raise RuntimeError("Unknown encoder 'libx264'")
            return orig(cmd, *a, **k)
        clips.run = patched
        try:
            out2 = clips.extract_preview(src, 0.5, 1.5, dest, tools, "video")
            assert os.path.exists(out2) and os.path.getsize(out2) > 0
        finally:
            clips.run = orig


def test_preview_error_is_descriptive():
    tools = detect_tools()
    if not tools.has_ffmpeg:
        return
    with tempfile.TemporaryDirectory() as d:
        try:
            clips.extract_preview(os.path.join(d, "missing.mp4"), 0, 2, d, tools, "video")
        except Exception as exc:
            assert "preview" in str(exc).lower()
        else:
            raise AssertionError("expected a failure for a missing source")

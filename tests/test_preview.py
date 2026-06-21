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


def _has_encoder(tools, name: str) -> bool:
    out = subprocess.run([tools.ffmpeg, "-v", "error", "-encoders"],
                         capture_output=True, text=True).stdout
    return name in out


def test_remove_segments_mp3_keeps_valid_container():
    """Trimming an MP3 must produce a valid MP3 (codec matched to container),
    not AAC forced into an .mp3 — the bug that broke removal."""
    tools = detect_tools()
    if not tools.has_ffmpeg or not _has_encoder(tools, "libmp3lame"):
        return
    from fluffless.clips import remove_segments
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "ep.mp3")
        subprocess.run([tools.ffmpeg, "-v", "error", "-y", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=20", "-c:a", "libmp3lame", src],
                       check=True, capture_output=True)
        out = remove_segments(src, [(5.0, 12.0)], 20.0, os.path.join(d, "out"), tools, "audio")
        assert out.endswith(".mp3") and os.path.getsize(out) > 0
        # Output is a real MP3 audio stream and roughly 13s (20 - 7 removed).
        probe = subprocess.run(
            [tools.ffprobe, "-v", "error", "-show_entries",
             "stream=codec_name:format=duration", "-of", "default=nw=1", out],
            capture_output=True, text=True).stdout
        assert "codec_name=mp3" in probe
        dur = float([l for l in probe.splitlines() if l.startswith("duration=")][0].split("=")[1])
        assert 12.0 < dur < 14.0


def test_preview_audio_with_embedded_cover_art():
    """An MP3/M4A with embedded album art must still preview (the cover-art
    video stream is dropped, not forced into an incompatible container)."""
    tools = detect_tools()
    if not tools.has_ffmpeg or not _has_encoder(tools, "libmp3lame"):
        return
    with tempfile.TemporaryDirectory() as d:
        audio = os.path.join(d, "a.mp3")
        cover = os.path.join(d, "cover.png")
        song = os.path.join(d, "song.mp3")
        subprocess.run([tools.ffmpeg, "-v", "error", "-y", "-f", "lavfi",
                        "-i", "sine=frequency=440:duration=4", "-c:a", "libmp3lame", audio],
                       check=True, capture_output=True)
        subprocess.run([tools.ffmpeg, "-v", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=red:size=120x120:duration=1", "-frames:v", "1", cover],
                       check=True, capture_output=True)
        subprocess.run([tools.ffmpeg, "-v", "error", "-y", "-i", audio, "-i", cover,
                        "-map", "0:a", "-map", "1:v", "-c", "copy",
                        "-disposition:v:0", "attached_pic", song],
                       check=True, capture_output=True)

        out = clips.extract_preview(song, 0.5, 2.5, os.path.join(d, "prev"), tools, "audio")
        assert os.path.exists(out) and os.path.getsize(out) > 0
        # The preview must carry audio and no leftover cover-art video stream.
        probe = subprocess.run(
            [tools.ffprobe, "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", out], capture_output=True, text=True).stdout
        assert "audio" in probe and "video" not in probe


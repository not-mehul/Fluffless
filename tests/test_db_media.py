"""Tests for the storage and media-classification layers (no external tools)."""

import json
import os
import tempfile

from fluffless.binaries import Tools
from fluffless.clips import _complement
from fluffless.db import Database
from fluffless.media import classify_ext, scan_library


def test_classify_ext():
    assert classify_ext("a.mp3") == "audio"
    assert classify_ext("b.MKV") == "video"
    assert classify_ext("c.txt") is None


def test_scan_library_groups_folders():
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "Show A"))
        os.makedirs(os.path.join(root, "Show B"))
        open(os.path.join(root, "Show A", "ep1.mp3"), "w").close()
        open(os.path.join(root, "Show A", "ep2.mp3"), "w").close()
        open(os.path.join(root, "Show B", "clip.mp4"), "w").close()
        open(os.path.join(root, "loose.m4a"), "w").close()
        # No ffmpeg → falls back to extension classification.
        tools = Tools(ffmpeg=None, ffprobe=None, fpcalc=None)
        folders = scan_library(root, tools)
        by_name = {f.name: f for f in folders}
        assert set(by_name) == {"Show A", "Show B", "(root)"}
        assert by_name["Show A"].kind == "audio"
        assert by_name["Show B"].kind == "video"
        assert len(by_name["Show A"].files) == 2


def test_scan_skips_output_dirs():
    with tempfile.TemporaryDirectory() as root:
        show = os.path.join(root, "Show")
        os.makedirs(os.path.join(show, "_fluffless_out"))
        open(os.path.join(show, "ep1.mp3"), "w").close()
        open(os.path.join(show, "_fluffless_out", "ep1.mp3"), "w").close()
        tools = Tools(ffmpeg=None, ffprobe=None, fpcalc=None)
        folders = scan_library(root, tools)
        assert len(folders[0].files) == 1  # the trimmed copy is not re-scanned


def test_database_roundtrip_and_export():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        pid = db.add_pattern(root, "Show", [1, 2, 3, 4], 0.1238, 32, 6.0, label="Other")
        db.add_clip(pid, "/x/ep1.mp3", 3.0, 9.0, preview="ep1.m4a")
        db.set_label(pid, "Ad")
        db.bump_pattern(pid)

        rows = db.patterns("Show")
        assert len(rows) == 1
        assert rows[0]["label"] == "Ad"
        assert rows[0]["shows"] == 2
        assert db.pattern_items(rows[0]) == [1, 2, 3, 4]
        assert db.clip_exists(pid, "/x/ep1.mp3", 3.0)
        assert not db.clip_exists(pid, "/x/ep1.mp3", 50.0)

        db.add_processed("/x/ep1.mp3", "/out/ep1.mp3",
                         [{"start": 3.0, "end": 9.0, "label": "Ad"}], 6.0)
        assert db.is_processed("/x/ep1.mp3")

        export = json.loads(db.export_json())
        assert export["patterns"][0]["label"] == "Ad"
        assert len(export["clips"]) == 1
        md = db.export_markdown()
        assert "Ad" in md and "ep1.mp3" in md
        db.close()


def test_complement_keep_ranges():
    # Remove 3-9 and 12-15 from a 20s file → keep [0-3], [9-12], [15-20].
    keep = _complement([(3, 9), (12, 15)], 20.0)
    assert keep == [(0.0, 3.0), (9.0, 12.0), (15.0, 20.0)]
    # Overlapping removals merge.
    assert _complement([(0, 5), (4, 8)], 10.0) == [(8.0, 10.0)]

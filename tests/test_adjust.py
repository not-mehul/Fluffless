"""Tests for boundary refinement: per-clip edits and pattern-level trimming."""

import tempfile

from fluffless.db import Database


def test_update_clip_bounds_clears_preview():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        pid = db.add_pattern(root, "Show", [1, 2, 3, 4, 5], 0.1, 32, 0.5)
        cid = db.add_clip(pid, "/x/ep1.mp3", 3.0, 9.0, preview="old.m4a")
        db.update_clip_bounds(cid, 4.0, 8.0)
        c = db.clip(cid)
        assert (c["start"], c["end"]) == (4.0, 8.0)
        assert c["preview"] is None        # stale preview dropped
        db.close()


def test_trim_pattern_tightens_fingerprint_and_clips():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        items = list(range(40))            # item_sec 0.1 → 40 items = 4.0s
        pid = db.add_pattern(root, "Show", items, 0.1, 32, 4.0)
        c1 = db.add_clip(pid, "/x/ep1.mp3", 10.0, 14.0, preview="a.m4a")
        c2 = db.add_clip(pid, "/x/ep2.mp3", 22.0, 26.0, preview="b.m4a")

        n = db.trim_pattern(pid, head=1.0, tail=0.5)   # 10 items off front, 5 off back
        assert n == 2

        row = db.pattern(pid)
        assert db.pattern_items(row) == list(range(10, 35))   # items[10:35]
        assert abs(row["duration"] - 2.5) < 1e-6

        a, b = db.clips(pid)
        assert (round(a["start"], 2), round(a["end"], 2)) == (11.0, 13.5)
        assert (round(b["start"], 2), round(b["end"], 2)) == (23.0, 25.5)
        assert a["preview"] is None and b["preview"] is None   # previews cleared
        db.close()


def test_trim_pattern_never_collapses_a_clip():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        pid = db.add_pattern(root, "Show", list(range(20)), 0.1, 32, 2.0)
        cid = db.add_clip(pid, "/x/ep1.mp3", 5.0, 6.0)
        db.trim_pattern(pid, head=5.0, tail=5.0)   # would over-trim a 1s clip
        c = db.clip(cid)
        assert c["end"] - c["start"] >= 0.2 - 1e-6   # kept a minimum sliver
        # The stored fingerprint keeps at least one item too.
        assert len(db.pattern_items(db.pattern(pid))) >= 1
        db.close()


def test_propagate_from_clip_applies_same_trim_to_all():
    import tempfile as _tf
    with _tf.TemporaryDirectory() as root:
        db = Database.open(root)
        pid = db.add_pattern(root, "Show", list(range(40)), 0.1, 32, 4.0, "Ad")
        a = db.add_clip(pid, "/x/ep1.mp3", 10.0, 14.0)
        b = db.add_clip(pid, "/x/ep2.mp3", 22.0, 26.0)
        c = db.add_clip(pid, "/x/ep3.mp3", 5.0, 9.0)
        db.update_clip_bounds(a, 11.5, 13.0)            # refine one: -1.5 head, -1.0 tail
        n, head, tail = db.propagate_from_clip(a)
        assert (n, head, tail) == (3, 1.5, 1.0)
        # every clip shifts from its OWN detected bounds by the same amounts
        assert (round(db.clip(b)["start"], 2), round(db.clip(b)["end"], 2)) == (23.5, 25.0)
        assert (round(db.clip(c)["start"], 2), round(db.clip(c)["end"], 2)) == (6.5, 8.0)
        # fingerprint tightened to the refined length, and it is idempotent
        assert len(db.pattern_items(db.pattern(pid))) == 15
        db.propagate_from_clip(a)
        assert (round(db.clip(b)["start"], 2), round(db.clip(b)["end"], 2)) == (23.5, 25.0)
        db.close()

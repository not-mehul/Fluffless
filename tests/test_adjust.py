"""Tests for boundary refinement: per-clip edits and pattern-level trimming."""

import tempfile

from fluffless.db import Database
from fluffless.repetition import Fingerprint


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
        pid = db.add_pattern(root, "Show", list(range(40)), 0.1, 32, 4.0)
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


def test_set_fingerprint_from_clip_pins_cropped_region():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        # Pattern detected as a long 4.0s segment, but the real ad is a sub-span.
        pid = db.add_pattern(root, "Show", list(range(40)), 0.1, 32, 4.0)
        db.store_fingerprint(
            "/x/ep1.mp3", "Show",
            Fingerprint(items=list(range(100)), item_sec=0.1, bits=32),
        )
        cid = db.add_clip(pid, "/x/ep1.mp3", 1.0, 3.0)   # user crops to [1.0, 3.0)

        res = db.set_fingerprint_from_clip(cid)
        assert res and res["items"] == 20                # items[10:30]
        row = db.pattern(pid)
        assert db.pattern_items(row) == list(range(10, 30))
        assert row["pinned"] == 1
        # The baseline is preserved, so reset can recover the full detection.
        assert db.reset_pattern(pid) == 1
        row = db.pattern(pid)
        assert db.pattern_items(row) == list(range(40))
        assert row["pinned"] == 0
        db.close()


def test_set_fingerprint_without_cache_reports_error():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        pid = db.add_pattern(root, "Show", list(range(40)), 0.1, 32, 4.0)
        cid = db.add_clip(pid, "/x/ep1.mp3", 1.0, 3.0)
        res = db.set_fingerprint_from_clip(cid)        # no cached fingerprint
        assert res and "error" in res
        assert db.pattern(pid)["pinned"] == 0          # nothing changed
        db.close()


def test_reset_clip_restores_detected_bounds():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        pid = db.add_pattern(root, "Show", list(range(20)), 0.1, 32, 2.0)
        cid = db.add_clip(pid, "/x/ep1.mp3", 5.0, 9.0, preview="p.m4a")
        db.update_clip_bounds(cid, 6.0, 7.5)
        os_, oe = db.reset_clip(cid)
        assert (os_, oe) == (5.0, 9.0)
        c = db.clip(cid)
        assert (c["start"], c["end"]) == (5.0, 9.0)
        assert c["preview"] is None
        db.close()


def test_move_clip_between_groups_and_recounts():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        a = db.add_pattern(root, "Show", list(range(20)), 0.1, 32, 2.0)
        b = db.add_pattern(root, "Show", list(range(20)), 0.1, 32, 2.0)
        db.add_clip(a, "/x/ep1.mp3", 1.0, 3.0)
        c2 = db.add_clip(a, "/x/ep2.mp3", 1.0, 3.0)

        res = db.move_clip(c2, b)
        assert res["moved"] and res["deleted_source"] is False
        assert db.clip(c2)["pattern_id"] == b
        assert db.pattern(a)["shows"] == 1     # recounted to remaining clips
        assert db.pattern(b)["shows"] == 1
        db.close()


def test_move_last_clip_deletes_empty_source():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        a = db.add_pattern(root, "Show", list(range(20)), 0.1, 32, 2.0)
        b = db.add_pattern(root, "Show", list(range(20)), 0.1, 32, 2.0)
        cid = db.add_clip(a, "/x/ep1.mp3", 1.0, 3.0)
        res = db.move_clip(cid, b)
        assert res["deleted_source"] is True
        assert db.pattern(a) is None
        db.close()


def test_new_group_from_clip_splits_out():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        pid = db.add_pattern(root, "Show", list(range(40)), 0.1, 32, 4.0)
        db.store_fingerprint(
            "/x/ep2.mp3", "Show",
            Fingerprint(items=list(range(100)), item_sec=0.1, bits=32),
        )
        db.add_clip(pid, "/x/ep1.mp3", 1.0, 5.0)
        c2 = db.add_clip(pid, "/x/ep2.mp3", 2.0, 4.0)

        res = db.new_group_from_clip(c2, status="pending")
        new_id = res["new_pattern_id"]
        assert new_id != pid and res["deleted_source"] is False
        assert db.clip(c2)["pattern_id"] == new_id
        new_row = db.pattern(new_id)
        assert new_row["status"] == "pending" and new_row["pinned"] == 1
        assert db.pattern_items(new_row) == list(range(20, 40))   # items[20:40]
        assert db.pattern(pid)["shows"] == 1                      # source recounted
        db.close()

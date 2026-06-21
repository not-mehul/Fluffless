"""Tests for per-clip edits and the clip re-grouping primitives that back
'Match across all' (move_clip / new_group_from_clip)."""

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

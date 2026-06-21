"""Tests for dedupe_contained_clips: collapsing nested detections."""

import tempfile

from fluffless.db import Database
from fluffless.repetition import Fingerprint
from fluffless.scan import dedupe_contained_clips


def _db(root):
    db = Database.open(root)
    # A cached fingerprint so new_group_from_clip etc. could work if needed.
    db.store_fingerprint("/x/ep0.mp3", "Show", Fingerprint([0] * 10, 0.1239, 32))
    return db


def test_block_covered_by_two_atomic_ads_is_dropped():
    """The classic case: ad1 + ad2 also detected as one back-to-back block.
    The block is fully covered by the two atomic ads, so it's removed."""
    with tempfile.TemporaryDirectory() as root:
        db = _db(root)
        isec = 0.1239
        first = db.add_pattern(root, "Show", [], isec, 32, 71.0, "confirmed")
        second = db.add_pattern(root, "Show", [], isec, 32, 74.0, "confirmed")
        block = db.add_pattern(root, "Show", [], isec, 32, 145.0, "confirmed")
        # Same file: atomic [190,261] + [261.8,335.7], block [190.4,333.7].
        db.add_clip(first, "/x/ep0.mp3", 190.0, 261.0)
        db.add_clip(second, "/x/ep0.mp3", 261.8, 335.7)
        db.add_clip(block, "/x/ep0.mp3", 190.4, 333.7)

        removed, deleted = dedupe_contained_clips(db, "Show")
        assert removed == 1
        assert deleted == [block]                  # block group emptied & deleted
        assert db.pattern(block) is None
        assert len(db.clips(first)) == 1           # atomic ads untouched
        assert len(db.clips(second)) == 1
        db.close()


def test_atomic_ads_are_never_dropped():
    """Nothing smaller covers the atomic ads, so they always survive."""
    with tempfile.TemporaryDirectory() as root:
        db = _db(root)
        isec = 0.1239
        first = db.add_pattern(root, "Show", [], isec, 32, 71.0, "confirmed")
        block = db.add_pattern(root, "Show", [], isec, 32, 145.0, "confirmed")
        db.add_clip(first, "/x/ep0.mp3", 190.0, 261.0)
        db.add_clip(block, "/x/ep0.mp3", 190.0, 335.0)
        # Block is NOT fully covered (only its first half overlaps an atomic ad).
        removed, deleted = dedupe_contained_clips(db, "Show")
        assert removed == 0
        assert len(db.clips(first)) == 1 and len(db.clips(block)) == 1
        db.close()


def test_does_not_cross_status_boundaries():
    """A confirmed block is not dropped by a smaller *dismissed* clip — that
    would leave ad audio uncut, since dismissed clips aren't removed."""
    with tempfile.TemporaryDirectory() as root:
        db = _db(root)
        isec = 0.1239
        block = db.add_pattern(root, "Show", [], isec, 32, 145.0, "confirmed")
        a = db.add_pattern(root, "Show", [], isec, 32, 71.0, "dismissed")
        b = db.add_pattern(root, "Show", [], isec, 32, 74.0, "dismissed")
        db.add_clip(block, "/x/ep0.mp3", 190.0, 335.0)
        db.add_clip(a, "/x/ep0.mp3", 190.0, 261.0)
        db.add_clip(b, "/x/ep0.mp3", 261.0, 335.0)
        removed, deleted = dedupe_contained_clips(db, "Show")
        assert removed == 0                        # status mismatch ⇒ no drop
        db.close()


def test_small_gap_within_tolerance_still_collapses():
    """A sub-second gap between the two atomic ads doesn't block the collapse."""
    with tempfile.TemporaryDirectory() as root:
        db = _db(root)
        isec = 0.1239
        first = db.add_pattern(root, "Show", [], isec, 32, 71.0, "confirmed")
        second = db.add_pattern(root, "Show", [], isec, 32, 71.0, "confirmed")
        block = db.add_pattern(root, "Show", [], isec, 32, 143.0, "confirmed")
        db.add_clip(first, "/x/ep0.mp3", 190.0, 261.0)
        db.add_clip(second, "/x/ep0.mp3", 261.8, 333.0)   # 0.8s gap
        db.add_clip(block, "/x/ep0.mp3", 190.0, 333.0)
        removed, _ = dedupe_contained_clips(db, "Show")
        assert removed == 1
        db.close()


def test_genuine_internal_gap_keeps_block():
    """A real multi-second gap (content between ads) is NOT covered, so the
    enclosing block is preserved — we never silently stop removing that gap."""
    with tempfile.TemporaryDirectory() as root:
        db = _db(root)
        isec = 0.1239
        first = db.add_pattern(root, "Show", [], isec, 32, 40.0, "confirmed")
        second = db.add_pattern(root, "Show", [], isec, 32, 40.0, "confirmed")
        block = db.add_pattern(root, "Show", [], isec, 32, 100.0, "confirmed")
        db.add_clip(first, "/x/ep0.mp3", 100.0, 140.0)
        db.add_clip(second, "/x/ep0.mp3", 160.0, 200.0)   # 20s gap
        db.add_clip(block, "/x/ep0.mp3", 100.0, 200.0)
        removed, _ = dedupe_contained_clips(db, "Show")
        assert removed == 0
        db.close()

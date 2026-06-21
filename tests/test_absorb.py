"""Tests for absorb_overlapping_pending: auto-dismissal of redundant cards."""

import tempfile

from fluffless.db import Database
from fluffless.repetition import Fingerprint
from fluffless.scan import absorb_overlapping_pending


def _db_with_fp(root):
    db = Database.open(root)
    isec = 0.1239
    items = [0] * 10
    db.store_fingerprint("/x/ep0.mp3", "Show", Fingerprint(items, isec, 32))
    db.store_fingerprint("/x/ep1.mp3", "Show", Fingerprint(items, isec, 32))
    return db, isec


def test_absorb_fully_covered_pending():
    """Pending pattern whose clips all fall inside confirmed clips → dismissed."""
    with tempfile.TemporaryDirectory() as root:
        db, isec = _db_with_fp(root)

        pid_c = db.add_pattern(root, "Show", [], isec, 32, 60.0, "confirmed")
        db.add_clip(pid_c, "/x/ep0.mp3", 10.0, 70.0)
        db.add_clip(pid_c, "/x/ep1.mp3", 10.0, 70.0)

        pid_p = db.add_pattern(root, "Show", [], isec, 32, 55.0, "pending")
        db.add_clip(pid_p, "/x/ep0.mp3", 12.0, 68.0)
        db.add_clip(pid_p, "/x/ep1.mp3", 13.0, 67.0)

        absorbed = absorb_overlapping_pending(db, pid_c)
        assert pid_p in absorbed
        assert db.pattern(pid_p)["status"] == "dismissed"
        db.close()


def test_absorb_non_overlapping_not_dismissed():
    """Pending clips far from confirmed clips → not dismissed."""
    with tempfile.TemporaryDirectory() as root:
        db, isec = _db_with_fp(root)

        pid_c = db.add_pattern(root, "Show", [], isec, 32, 60.0, "confirmed")
        db.add_clip(pid_c, "/x/ep0.mp3", 10.0, 70.0)

        pid_p = db.add_pattern(root, "Show", [], isec, 32, 30.0, "pending")
        db.add_clip(pid_p, "/x/ep0.mp3", 200.0, 230.0)

        absorbed = absorb_overlapping_pending(db, pid_c)
        assert pid_p not in absorbed
        assert db.pattern(pid_p)["status"] == "pending"
        db.close()


def test_absorb_partial_coverage_below_threshold_not_dismissed():
    """50% of clips covered is below the 80% default threshold → stays pending."""
    with tempfile.TemporaryDirectory() as root:
        db, isec = _db_with_fp(root)

        pid_c = db.add_pattern(root, "Show", [], isec, 32, 60.0, "confirmed")
        db.add_clip(pid_c, "/x/ep0.mp3", 10.0, 70.0)
        # No clip for ep1 in the confirmed pattern.

        pid_p = db.add_pattern(root, "Show", [], isec, 32, 60.0, "pending")
        db.add_clip(pid_p, "/x/ep0.mp3", 12.0, 68.0)   # covered
        db.add_clip(pid_p, "/x/ep1.mp3", 12.0, 68.0)   # not covered

        absorbed = absorb_overlapping_pending(db, pid_c)
        assert pid_p not in absorbed
        assert db.pattern(pid_p)["status"] == "pending"
        db.close()


def test_absorb_high_coverage_dismissed_with_custom_threshold():
    """With threshold=0.5, a 50%-covered pattern is absorbed."""
    with tempfile.TemporaryDirectory() as root:
        db, isec = _db_with_fp(root)

        pid_c = db.add_pattern(root, "Show", [], isec, 32, 60.0, "confirmed")
        db.add_clip(pid_c, "/x/ep0.mp3", 10.0, 70.0)

        pid_p = db.add_pattern(root, "Show", [], isec, 32, 60.0, "pending")
        db.add_clip(pid_p, "/x/ep0.mp3", 12.0, 68.0)   # covered
        db.add_clip(pid_p, "/x/ep1.mp3", 12.0, 68.0)   # not covered

        absorbed = absorb_overlapping_pending(db, pid_c, overlap_threshold=0.5)
        assert pid_p in absorbed
        assert db.pattern(pid_p)["status"] == "dismissed"
        db.close()


def test_absorb_does_not_touch_confirmed_or_dismissed():
    """Only pending patterns are absorbed — confirmed and dismissed are left alone."""
    with tempfile.TemporaryDirectory() as root:
        db, isec = _db_with_fp(root)

        pid_c = db.add_pattern(root, "Show", [], isec, 32, 60.0, "confirmed")
        db.add_clip(pid_c, "/x/ep0.mp3", 10.0, 70.0)

        pid2 = db.add_pattern(root, "Show", [], isec, 32, 60.0, "confirmed")
        db.add_clip(pid2, "/x/ep0.mp3", 12.0, 68.0)

        pid3 = db.add_pattern(root, "Show", [], isec, 32, 60.0, "dismissed")
        db.add_clip(pid3, "/x/ep0.mp3", 11.0, 69.0)

        absorbed = absorb_overlapping_pending(db, pid_c)
        assert absorbed == []
        assert db.pattern(pid2)["status"] == "confirmed"
        assert db.pattern(pid3)["status"] == "dismissed"
        db.close()


def test_absorb_skips_own_pattern():
    """The confirmed pattern itself is never absorbed."""
    with tempfile.TemporaryDirectory() as root:
        db, isec = _db_with_fp(root)

        pid_c = db.add_pattern(root, "Show", [], isec, 32, 60.0, "confirmed")
        db.add_clip(pid_c, "/x/ep0.mp3", 10.0, 70.0)

        absorbed = absorb_overlapping_pending(db, pid_c)
        assert pid_c not in absorbed
        assert db.pattern(pid_c)["status"] == "confirmed"
        db.close()


def test_absorb_partial_clip_overlap_below_threshold():
    """A pending clip that only 40% overlaps the confirmed clip is not covered."""
    with tempfile.TemporaryDirectory() as root:
        db, isec = _db_with_fp(root)

        # Confirmed: [10, 70] = 60s. Pending: [50, 110] = 60s.
        # Overlap = [50,70] = 20s. ratio = 20/60 = 0.33 — below 0.80.
        pid_c = db.add_pattern(root, "Show", [], isec, 32, 60.0, "confirmed")
        db.add_clip(pid_c, "/x/ep0.mp3", 10.0, 70.0)

        pid_p = db.add_pattern(root, "Show", [], isec, 32, 60.0, "pending")
        db.add_clip(pid_p, "/x/ep0.mp3", 50.0, 110.0)

        absorbed = absorb_overlapping_pending(db, pid_c)
        assert pid_p not in absorbed
        assert db.pattern(pid_p)["status"] == "pending"
        db.close()


def test_absorb_multiple_pending_only_overlapping_dismissed():
    """Multiple pending patterns: only overlapping ones are dismissed."""
    with tempfile.TemporaryDirectory() as root:
        db, isec = _db_with_fp(root)

        pid_c = db.add_pattern(root, "Show", [], isec, 32, 60.0, "confirmed")
        db.add_clip(pid_c, "/x/ep0.mp3", 10.0, 70.0)
        db.add_clip(pid_c, "/x/ep1.mp3", 10.0, 70.0)

        # Overlapping — should be absorbed.
        pid_overlap = db.add_pattern(root, "Show", [], isec, 32, 55.0, "pending")
        db.add_clip(pid_overlap, "/x/ep0.mp3", 12.0, 68.0)
        db.add_clip(pid_overlap, "/x/ep1.mp3", 13.0, 67.0)

        # Different segment — should stay pending.
        pid_other = db.add_pattern(root, "Show", [], isec, 32, 30.0, "pending")
        db.add_clip(pid_other, "/x/ep0.mp3", 300.0, 330.0)
        db.add_clip(pid_other, "/x/ep1.mp3", 300.0, 330.0)

        absorbed = absorb_overlapping_pending(db, pid_c)
        assert pid_overlap in absorbed
        assert pid_other not in absorbed
        assert db.pattern(pid_overlap)["status"] == "dismissed"
        assert db.pattern(pid_other)["status"] == "pending"
        db.close()

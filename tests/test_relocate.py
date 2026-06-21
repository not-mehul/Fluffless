"""Tests for relocate_group_from_clip: 'find this ad everywhere' re-derivation."""

import random
import tempfile

from fluffless.db import Database
from fluffless.repetition import Fingerprint
from fluffless.scan import relocate_group_from_clip


def _fuzz(rng, seg):
    """One-bit-flipped copy of a segment — a plausibly re-encoded airing."""
    return [v ^ (1 << rng.randint(0, 31)) for v in seg]


def _noise(rng, n):
    return [rng.getrandbits(32) for _ in range(n)]


def test_relocate_aligns_matches_pulls_in_and_splits_nonmatches():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        isec = 0.1239
        rng = random.Random(3)
        ad = [rng.getrandbits(32) for _ in range(500)]      # ~62s segment

        # Four files. A/B/C contain the ad; D does not.
        epA = _noise(rng, 80) + _fuzz(rng, ad) + _noise(rng, 200)
        epB = _noise(rng, 150) + _fuzz(rng, ad) + _noise(rng, 90)
        epC = _noise(rng, 60) + _fuzz(rng, ad) + _noise(rng, 300)
        epD = _noise(rng, 700)
        for name, items in [("A", epA), ("B", epB), ("C", epC), ("D", epD)]:
            db.store_fingerprint(f"/x/ep{name}.mp3", "Show",
                                 Fingerprint(items=items, item_sec=isec, bits=32))

        # A ragged group: A, B (over-captured) and D (a wrong member). C is NOT
        # in the group yet — relocation should pull it in.
        pid = db.add_pattern(root, "Show", ad, isec, 32, len(ad) * isec, "pending")
        a_clip = db.add_clip(pid, "/x/epA.mp3", 80 * isec, (80 + 560) * isec)   # ragged tail
        db.add_clip(pid, "/x/epB.mp3", 140 * isec, (140 + 540) * isec)          # ragged
        d_clip = db.add_clip(pid, "/x/epD.mp3", 100 * isec, 220 * isec)         # not the ad

        # Crop the reference clip (A) to the exact ad span, then relocate.
        db.update_clip_bounds(a_clip, 80 * isec, (80 + 500) * isec)
        res = relocate_group_from_clip(db, a_clip)

        assert "error" not in res and res["pattern_id"] == pid
        assert res["moved_out"] == 1          # epD split off
        assert res["added"] == 1              # epC pulled in
        assert res["leftover_group_id"] is not None

        clips = db.clips(pid)
        files = sorted(c["file_name"] for c in clips)
        assert files == ["epA.mp3", "epB.mp3", "epC.mp3"]   # D gone, C arrived

        # Every surviving clip is the same exact length.
        lens = [round(c["end"] - c["start"], 4) for c in clips]
        assert max(lens) - min(lens) < 1e-6

        # The non-match landed in the leftover group, alone.
        leftover = db.clips(res["leftover_group_id"])
        assert [c["id"] for c in leftover] == [d_clip]

        # The group's fingerprint is now pinned to the crop.
        assert db.pattern(pid)["pinned"] == 1
        assert db.pattern(pid)["shows"] == 3
        db.close()


def test_relocate_is_idempotent():
    """Relocating again over an already-aligned group changes nothing."""
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        isec = 0.1239
        rng = random.Random(7)
        ad = [rng.getrandbits(32) for _ in range(400)]
        epA = _noise(rng, 50) + _fuzz(rng, ad) + _noise(rng, 100)
        epB = _noise(rng, 90) + _fuzz(rng, ad) + _noise(rng, 60)
        db.store_fingerprint("/x/epA.mp3", "Show", Fingerprint(epA, isec, 32))
        db.store_fingerprint("/x/epB.mp3", "Show", Fingerprint(epB, isec, 32))

        pid = db.add_pattern(root, "Show", ad, isec, 32, len(ad) * isec, "pending")
        a_clip = db.add_clip(pid, "/x/epA.mp3", 50 * isec, (50 + 400) * isec)
        db.add_clip(pid, "/x/epB.mp3", 90 * isec, (90 + 400) * isec)

        first = relocate_group_from_clip(db, a_clip)
        assert first["moved_out"] == 0
        n_after = len(db.clips(pid))

        second = relocate_group_from_clip(db, a_clip)
        assert second["added"] == 0 and second["moved_out"] == 0
        assert len(db.clips(pid)) == n_after
        db.close()


def test_relocate_catches_repeat_airing_in_one_file():
    """An ad airing twice in one episode yields two aligned clips."""
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        isec = 0.1239
        rng = random.Random(9)
        ad = [rng.getrandbits(32) for _ in range(350)]
        ep = _noise(rng, 60) + _fuzz(rng, ad) + _noise(rng, 300) + _fuzz(rng, ad) + _noise(rng, 80)
        db.store_fingerprint("/x/ep0.mp3", "Show", Fingerprint(ep, isec, 32))

        pid = db.add_pattern(root, "Show", ad, isec, 32, len(ad) * isec, "pending")
        c0 = db.add_clip(pid, "/x/ep0.mp3", 60 * isec, (60 + 350) * isec)

        res = relocate_group_from_clip(db, c0)
        assert res["added"] == 1          # the second airing
        assert len(db.clips(pid)) == 2
        db.close()


def test_relocate_missing_fingerprint_reports_error():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        isec = 0.1239
        pid = db.add_pattern(root, "Show", [1, 2, 3], isec, 32, 3 * isec, "pending")
        cid = db.add_clip(pid, "/x/missing.mp3", 0.0, 5.0)   # no cached fingerprint
        res = relocate_group_from_clip(db, cid)
        assert "error" in res
        db.close()

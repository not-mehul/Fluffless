"""Tests for incremental scan mode — applying confirmed patterns to new files
without re-fingerprinting or re-running detection on the existing library."""

import random
import tempfile

from fluffless.db import Database
from fluffless.repetition import Fingerprint
from fluffless.scan import apply_pattern_to_stored


def _fuzz(rng, seg):
    return [v ^ (1 << rng.randint(0, 31)) for v in seg]


def _noise(rng, n):
    return [rng.getrandbits(32) for _ in range(n)]


def test_new_file_picks_up_confirmed_pattern():
    """A confirmed pattern found in N files is automatically located in a new
    file when that file's fingerprint is stored and apply_pattern_to_stored is run.
    This is the engine behind both full-scan and incremental mode."""
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        rng = random.Random(42)
        isec = 0.1239
        ad = [rng.getrandbits(32) for _ in range(600)]

        # Pre-existing library: 3 files already scanned.
        for k in range(3):
            items = _noise(rng, 80) + _fuzz(rng, ad) + _noise(rng, 150)
            db.store_fingerprint(f"/x/ep{k}.mp3", "Show",
                                 Fingerprint(items=items, item_sec=isec, bits=32))

        pid = db.add_pattern(root, "Show", ad, isec, 32, len(ad) * isec, "confirmed")
        apply_pattern_to_stored(db, pid)
        assert len(db.clips(pid)) == 3

        # New episode drops in — store its fingerprint (as incremental scan would).
        new_ep = _noise(rng, 120) + _fuzz(rng, ad) + _noise(rng, 200)
        db.store_fingerprint("/x/ep_new.mp3", "Show",
                             Fingerprint(items=new_ep, item_sec=isec, bits=32))
        assert db.has_fingerprint("/x/ep_new.mp3")

        # Applying confirmed patterns to stored fingerprints catches the new file.
        added = apply_pattern_to_stored(db, pid)
        assert added == 1
        file_names = {c["file_name"] for c in db.clips(pid)}
        assert "ep_new.mp3" in file_names
        assert len(db.clips(pid)) == 4
        db.close()


def test_already_scanned_file_is_not_recounted():
    """apply_pattern_to_stored is idempotent — a file already in the group
    does not gain a duplicate clip."""
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        rng = random.Random(99)
        isec = 0.1239
        ad = [rng.getrandbits(32) for _ in range(400)]

        items = _noise(rng, 60) + _fuzz(rng, ad) + _noise(rng, 100)
        db.store_fingerprint("/x/ep0.mp3", "Show",
                             Fingerprint(items=items, item_sec=isec, bits=32))
        pid = db.add_pattern(root, "Show", ad, isec, 32, len(ad) * isec, "confirmed")

        first = apply_pattern_to_stored(db, pid)
        second = apply_pattern_to_stored(db, pid)
        assert first == 1
        assert second == 0   # no duplicates
        assert len(db.clips(pid)) == 1
        db.close()


def test_new_file_without_ad_gets_no_clip():
    """A new episode that doesn't contain the confirmed ad gets no clip added."""
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        rng = random.Random(77)
        isec = 0.1239
        ad = [rng.getrandbits(32) for _ in range(500)]

        # Confirmed pattern from episode 0.
        has_ad = _noise(rng, 90) + _fuzz(rng, ad) + _noise(rng, 150)
        db.store_fingerprint("/x/ep0.mp3", "Show",
                             Fingerprint(items=has_ad, item_sec=isec, bits=32))
        pid = db.add_pattern(root, "Show", ad, isec, 32, len(ad) * isec, "confirmed")
        apply_pattern_to_stored(db, pid)

        # New episode — pure noise, no ad.
        no_ad = _noise(rng, 800)
        db.store_fingerprint("/x/ep_new.mp3", "Show",
                             Fingerprint(items=no_ad, item_sec=isec, bits=32))

        added = apply_pattern_to_stored(db, pid)
        assert added == 0
        file_names = {c["file_name"] for c in db.clips(pid)}
        assert "ep_new.mp3" not in file_names
        db.close()


def test_has_fingerprint_distinguishes_new_from_known():
    """has_fingerprint() correctly identifies which files need processing."""
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        rng = random.Random(55)
        isec = 0.1239
        fp = Fingerprint(items=_noise(rng, 200), item_sec=isec, bits=32)

        assert not db.has_fingerprint("/x/ep_new.mp3")
        db.store_fingerprint("/x/ep_new.mp3", "Show", fp)
        assert db.has_fingerprint("/x/ep_new.mp3")
        # A different path is still unscanned.
        assert not db.has_fingerprint("/x/ep_other.mp3")
        db.close()

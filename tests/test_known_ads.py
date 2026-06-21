"""Cached fingerprints + back-applying a known ad across stored files."""

import random
import tempfile

from fluffless.db import Database
from fluffless.repetition import DetectParams, Fingerprint
from fluffless.scan import apply_pattern_to_stored


def test_store_roundtrip_and_apply_known_ad():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        isec = 0.1239
        rng = random.Random(5)
        ad = [rng.getrandbits(32) for _ in range(1000)]

        for k in range(3):                       # 3 files all containing the ad
            head = [rng.getrandbits(32) for _ in range(rng.randint(50, 150))]
            items = head + [v ^ (1 << rng.randint(0, 31)) for v in ad] + \
                [rng.getrandbits(32) for _ in range(200)]
            db.store_fingerprint(f"/x/ep{k}.mp3", "Show",
                                 Fingerprint(items=items, item_sec=isec, bits=32))

        stored = db.fingerprints("Show")
        assert len(stored) == 3
        assert db.has_fingerprint("/x/ep0.mp3")

        # An Ad pattern with no clips yet → back-apply tags every cached file.
        pid = db.add_pattern(root, "Show", ad, isec, 32, len(ad) * isec, "Ad")
        assert apply_pattern_to_stored(db, pid) == 3
        clips = db.clips(pid)
        assert len(clips) == 3
        lens = [c["end"] - c["start"] for c in clips]
        assert max(lens) - min(lens) < 3.0        # consistent length
        # Idempotent: applying again adds nothing.
        assert apply_pattern_to_stored(db, pid) == 0
        db.close()


def test_apply_skips_files_without_the_ad():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        isec = 0.1239
        rng = random.Random(8)
        ad = [rng.getrandbits(32) for _ in range(900)]
        # one file has the ad, one does not
        with_ad = [rng.getrandbits(32) for _ in range(80)] + \
            [v ^ (1 << rng.randint(0, 31)) for v in ad]
        without = [rng.getrandbits(32) for _ in range(1200)]
        db.store_fingerprint("/x/a.mp3", "Show", Fingerprint(with_ad, isec, 32))
        db.store_fingerprint("/x/b.mp3", "Show", Fingerprint(without, isec, 32))
        pid = db.add_pattern(root, "Show", ad, isec, 32, len(ad) * isec, "Ad")
        assert apply_pattern_to_stored(db, pid) == 1     # only the file with the ad
        db.close()

"""Cached fingerprints + back-applying a known ad across stored files."""

import random
import tempfile

from fluffless.db import Database
from fluffless.repetition import DetectParams, Fingerprint, locate_all
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
        pid = db.add_pattern(root, "Show", ad, isec, 32, len(ad) * isec, "confirmed")
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
        pid = db.add_pattern(root, "Show", ad, isec, 32, len(ad) * isec, "confirmed")
        assert apply_pattern_to_stored(db, pid) == 1     # only the file with the ad
        db.close()


def test_locate_all_finds_repeat_airings_in_one_file():
    """The same ad airing twice in one episode yields two distinct hits."""
    rng = random.Random(11)
    ad = [rng.getrandbits(32) for _ in range(400)]
    noise = lambda n: [rng.getrandbits(32) for _ in range(n)]
    fuzz = lambda seg: [v ^ (1 << rng.randint(0, 31)) for v in seg]
    items = noise(120) + fuzz(ad) + noise(300) + fuzz(ad) + noise(150)
    p = DetectParams()
    hits = locate_all(ad, items, p)
    assert len(hits) == 2
    (s0, e0), (s1, e1) = hits
    assert e0 <= s1                       # sorted, non-overlapping
    assert abs((e0 - s0) - len(ad)) < 30  # each spans roughly the ad length
    assert abs(s0 - 120) < 30 and abs(s1 - 820) < 40


def test_confirm_rescan_cuts_every_airing():
    """A confirmed segment is back-applied to every airing across files —
    including a file where it plays twice."""
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        isec = 0.1239
        rng = random.Random(12)
        ad = [rng.getrandbits(32) for _ in range(500)]
        fuzz = lambda seg: [v ^ (1 << rng.randint(0, 31)) for v in seg]
        noise = lambda n: [rng.getrandbits(32) for _ in range(n)]
        # ep0: ad once.  ep1: ad twice (two breaks).
        ep0 = noise(60) + fuzz(ad) + noise(120)
        ep1 = noise(90) + fuzz(ad) + noise(220) + fuzz(ad) + noise(80)
        db.store_fingerprint("/x/ep0.mp3", "Show", Fingerprint(ep0, isec, 32))
        db.store_fingerprint("/x/ep1.mp3", "Show", Fingerprint(ep1, isec, 32))

        pid = db.add_pattern(root, "Show", ad, isec, 32, len(ad) * isec, "confirmed")
        added = apply_pattern_to_stored(db, pid)
        assert added == 3                          # 1 in ep0 + 2 in ep1
        by_file = {}
        for c in db.clips(pid):
            by_file.setdefault(c["file_name"], 0)
            by_file[c["file_name"]] += 1
        assert by_file == {"ep0.mp3": 1, "ep1.mp3": 2}
        db.close()

"""Length-aware pattern grouping: a short ad contained in a longer combined
segment must NOT be merged into it (they are different audio)."""

import random
import tempfile

from fluffless.db import Database
from fluffless.media import MediaFile
from fluffless.repetition import DetectParams, Fingerprint
from fluffless.scan import _store_segment


def test_short_and_long_segments_stay_separate():
    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        p = DetectParams()
        item_sec = 0.1239
        rng = random.Random(1)
        items = [rng.getrandbits(32) for _ in range(1100)]   # ~136s, realistic
        fp = Fingerprint(items=items, item_sec=item_sec, bits=32)

        mf1 = MediaFile(path="/x/ep1.mp3", name="ep1.mp3", kind="audio")
        mf2 = MediaFile(path="/x/ep2.mp3", name="ep2.mp3", kind="audio")
        mf3 = MediaFile(path="/x/ep3.mp3", name="ep3.mp3", kind="audio")

        # A long ~136s segment → pattern A.
        a = _store_segment(db, root, "Show", mf1, fp, 0.0, 1100 * item_sec, p, None)
        # A ~74s slice — a subset of A (high match ratio) but very different
        # length → must become its OWN pattern, not merge.
        b = _store_segment(db, root, "Show", mf2, fp, 0.0, 600 * item_sec, p, None)
        assert a != b
        assert len(db.patterns("Show")) == 2

        # Another ~136s occurrence → similar length & match → merges into A.
        c = _store_segment(db, root, "Show", mf3, fp, 0.0, 1100 * item_sec, p, None)
        assert c == a
        assert len(db.patterns("Show")) == 2
        durs = sorted(round(r["duration"]) for r in db.patterns("Show"))
        assert durs[0] < 0.85 * durs[1]           # clearly distinct lengths
        db.close()

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


def test_normalize_pattern_lengths_fixes_clipped_outlier():
    """A clip whose boundary was detected short is re-located to the canonical
    fingerprint, so all occurrences of one ad end up the same length."""
    import random
    from fluffless.repetition import Fingerprint
    from fluffless.scan import _normalize_pattern_lengths

    rng = random.Random(3)
    ad = [rng.getrandbits(32) for _ in range(1000)]
    isec = 0.1239
    fps = {}
    layout = []
    for k in range(4):
        head = [rng.getrandbits(32) for _ in range(rng.randint(50, 200))]
        items = head + [v ^ (1 << rng.randint(0, 31)) for v in ad] + \
            [rng.getrandbits(32) for _ in range(300)]
        path = f"/x/ep{k}.mp3"
        fps[path] = Fingerprint(items=items, item_sec=isec, bits=32)
        layout.append((path, len(head)))

    with tempfile.TemporaryDirectory() as root:
        db = Database.open(root)
        p = DetectParams()
        pid = db.add_pattern(root, "Show", ad, isec, 32, 1000 * isec, "confirmed")
        for i, (path, head) in enumerate(layout):
            length = 700 if i == 3 else 1000          # ep3 clipped short
            db.add_clip(pid, path, head * isec, (head + length) * isec)
        _normalize_pattern_lengths(db, [pid], fps, p)
        lengths = sorted(round(c["end"] - c["start"], 1) for c in db.clips(pid))
        assert max(lengths) - min(lengths) < 2.0       # all consistent now
        # The pattern's stored duration tracks its clips (no fingerprint/clip
        # length mismatch).
        pat = db.pattern(pid)
        assert abs(pat["duration"] - lengths[0]) < 2.0
        assert abs(len(db.pattern_items(pat)) * isec - lengths[0]) < 2.0
        db.close()

"""Deterministic tests for the matcher, using synthetic integer fingerprints.

We don't go through Chromaprint here — we hand-build item streams so the bit
math and run extraction are tested exactly, free of any audio-codec quirks.
"""

import random

from fluffless.repetition import (
    DetectParams,
    Fingerprint,
    best_ratio,
    candidate_offsets,
    coverage_at,
    items_match,
    locate,
    match_cover,
    recurring_segments,
    _runs,
)


def _rng_items(n, seed):
    r = random.Random(seed)
    return [r.getrandbits(32) for _ in range(n)]


def _flip_bits(items, k, seed):
    """Return items with k random bits flipped per item — simulates re-encode."""
    r = random.Random(seed)
    out = []
    for v in items:
        bits = r.sample(range(32), k)
        for b in bits:
            v ^= (1 << b)
        out.append(v & 0xFFFFFFFF)
    return out


def test_items_match_threshold():
    p = DetectParams()
    assert items_match(0, 0, p.max_bit_err, p.mask)
    assert items_match(0, 0b1111_1111, p.max_bit_err, p.mask)        # 8 bits
    assert not items_match(0, 0b1_1111_1111, p.max_bit_err, p.mask)  # 9 bits


def test_candidate_offsets_finds_true_offset():
    p = DetectParams()
    shared = _rng_items(60, 1)
    a = _rng_items(20, 2) + shared
    b = _rng_items(50, 3) + shared
    offs = candidate_offsets(a, b, p)
    assert (50 - 20) in offs  # B has shared 30 items later than A


def test_coverage_recovers_reencoded_segment():
    p = DetectParams()
    shared = _rng_items(60, 7)
    a = _rng_items(20, 8) + shared
    b = _rng_items(50, 9) + _flip_bits(shared, 3, 99)   # same intro, re-encoded
    cov = match_cover(a, b, p)
    # The shared tail of A (positions 20..80) should be densely covered.
    assert sum(cov[20:]) >= 55
    # The unique head of A should be essentially uncovered.
    assert sum(cov[:20]) <= 3


def test_runs_bridges_gaps_and_enforces_density():
    cover = [1] * 10 + [0] + [1] * 10           # one gap inside a solid run
    runs = _runs(cover, min_len=5, max_gap=2, min_density=0.5)
    assert runs == [(0, 21)]
    # Scattered specks must not become a run.
    sparse = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0]
    assert _runs(sparse, min_len=5, max_gap=2, min_density=0.5) == []


def test_recurring_segments_timestamps():
    p = DetectParams(min_seconds=2.0)
    shared = _rng_items(80, 11)            # 80 items ≈ 10s at 0.1238/item
    fps = []
    for k in range(3):
        head = _rng_items(10 * (k + 1), 100 + k)
        items = head + _flip_bits(shared, 2, 200 + k) + _rng_items(15, 300 + k)
        fps.append(Fingerprint(items=items, item_sec=0.1238, bits=32))
    segs = recurring_segments(fps, p)
    assert len(segs) == 3
    for k, ranges in enumerate(segs):
        assert len(ranges) == 1
        start, end = ranges[0]
        expected_start = (10 * (k + 1)) * 0.1238
        assert abs(start - expected_start) < 0.5
        assert (end - start) > 8.0


def test_locate_known_pattern_in_new_file():
    p = DetectParams()
    pattern = _rng_items(50, 21)
    b = _rng_items(30, 22) + _flip_bits(pattern, 3, 23) + _rng_items(20, 24)
    hit = locate(pattern, b, p)
    assert hit is not None
    start, end = hit
    assert abs(start - 30) <= 2
    assert abs(end - 80) <= 2


def test_locate_returns_none_when_absent():
    p = DetectParams()
    pattern = _rng_items(50, 31)
    b = _rng_items(200, 32)               # unrelated content
    assert locate(pattern, b, p) is None


def test_best_ratio_dedupe():
    p = DetectParams()
    pat = _rng_items(50, 41)
    same = _flip_bits(pat, 3, 42)
    other = _rng_items(50, 43)
    assert best_ratio(pat, same, p) >= 0.6
    assert best_ratio(pat, other, p) < 0.3


def test_video_params_scale_bit_error():
    p = DetectParams()
    v = p.scaled(64)
    assert v.bits == 64
    assert v.max_bit_err == 16        # 8/32 → 16/64
    assert v.key_mask == 0xFFFF000000000000


def test_parallel_detection_matches_serial():
    p = DetectParams(min_seconds=5.0)
    shared = _rng_items(60, 11)
    fps = []
    for k in range(8):                         # >= PARALLEL_MIN_FILES, so the
        head = _rng_items(10 * (k + 1), 100 + k)   # workers=2 run takes the
        body = _rng_items(300, 500 + k)            # process-pool path
        items = head + _flip_bits(shared, 2, 200 + k) + body
        fps.append(Fingerprint(items=items, item_sec=0.1238, bits=32))
    serial = recurring_segments(fps, p, workers=1)
    parallel = recurring_segments(fps, p, workers=2)
    norm = lambda S: [[(round(a, 2), round(b, 2)) for a, b in x] for x in S]
    assert norm(serial) == norm(parallel)
    assert any(serial)                          # actually found the shared run


def test_video_width_matching():
    p = DetectParams().scaled(64)
    shared = [random.Random(i).getrandbits(64) for i in range(40)]
    a = [random.Random(900 + i).getrandbits(64) for i in range(10)] + shared
    b = [random.Random(800 + i).getrandbits(64) for i in range(25)] + shared
    cov = match_cover(a, b, p)
    assert sum(cov[10:]) >= 35

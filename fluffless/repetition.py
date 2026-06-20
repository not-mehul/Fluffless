"""The detection engine — finding repeated sub-sequences in noisy fixed-rate
fingerprint streams and reporting their timestamps.

This module is deliberately *fingerprint-agnostic*. It assumes only a sequence
of fixed-width unsigned integers where the same content yields near-identical
integers (small Hamming distance) and unrelated content yields integers that
differ in about half their bits. Audio (via Chromaprint, 32-bit items) and
video (via perceptual frame hashing, 64-bit items) both produce such streams,
so the same alignment / recurrence / run-extraction code serves both.

See Pattern_Detection.md for the full rationale behind every constant here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence


@dataclass(frozen=True)
class DetectParams:
    """Tuning dials for detection. Defaults match Pattern_Detection.md §8."""

    bits: int = 32              # item width: 32 for audio, 64 for video pHash
    max_bit_err: int = 8        # bits two items may differ and still "match"
    min_seconds: float = 5.0    # minimum length of a reported segment
    min_shows: int = 2          # files a segment must appear in
    max_gap_seconds: float = 0.5  # gap bridged inside a segment
    min_density: float = 0.5    # fraction of a run that must be covered
    top_offsets: int = 8        # candidate alignments verified per pair
    locate_min_ratio: float = 0.55  # fraction of a pattern that must align to keep a hit
    dedupe_ratio: float = 0.6   # match ratio above which two patterns are "the same"

    @property
    def mask(self) -> int:
        return (1 << self.bits) - 1

    @property
    def key_mask(self) -> int:
        # Top 16 bits — stable enough to survive a few flipped bits when seeding.
        return self.mask ^ ((1 << (self.bits - 16)) - 1)

    def scaled(self, bits: int) -> "DetectParams":
        """Return a copy for a different item width, scaling max_bit_err.

        pHash matches commonly use ~10/64 bits where audio uses ~8/32, so the
        threshold tracks the width rather than staying fixed (§9, Option B).
        """
        if bits == self.bits:
            return self
        scaled_err = max(1, round(self.max_bit_err * bits / self.bits))
        return DetectParams(
            bits=bits,
            max_bit_err=scaled_err,
            min_seconds=self.min_seconds,
            min_shows=self.min_shows,
            max_gap_seconds=self.max_gap_seconds,
            min_density=self.min_density,
            top_offsets=self.top_offsets,
            locate_min_ratio=self.locate_min_ratio,
            dedupe_ratio=self.dedupe_ratio,
        )


@dataclass
class Fingerprint:
    """A fixed-rate stream of integer items plus its measured time resolution."""

    items: list[int] = field(default_factory=list)
    item_sec: float = 0.1238    # seconds represented by one item
    bits: int = 32

    def __len__(self) -> int:
        return len(self.items)

    def slice_seconds(self, start: float, end: float) -> list[int]:
        """The items covering [start, end) seconds — used to store a pattern."""
        i = max(0, round(start / self.item_sec))
        j = min(len(self.items), round(end / self.item_sec))
        return self.items[i:j]


# --- §3  Hamming-tolerant item matching --------------------------------------

def items_match(a: int, b: int, max_bit_err: int, mask: int) -> bool:
    """Two items match when they differ in at most ``max_bit_err`` bits."""
    return ((a ^ b) & mask).bit_count() <= max_bit_err


# --- §4.1  Seeding candidate offsets (vote histogram) ------------------------

def candidate_offsets(a: Sequence[int], b: Sequence[int], p: DetectParams) -> list[int]:
    """Cheaply surface likely alignment offsets ``d`` so that A[i] ~ B[i+d].

    Buckets B by a masked key, then every A-item votes for the offset of each
    B-item sharing its key. Peaks are the likely alignments. We keep several
    (top_offsets) because one file can share multiple segments with another.
    """
    key_mask = p.key_mask
    bpos: dict[int, list[int]] = defaultdict(list)
    for j, v in enumerate(b):
        bpos[v & key_mask].append(j)
    votes: Counter[int] = Counter()
    for i, v in enumerate(a):
        for j in bpos.get(v & key_mask, ()):  # noqa: B905
            votes[j - i] += 1
    return [off for off, _ in votes.most_common(p.top_offsets)]


# --- §4.2  Verifying an offset (coverage bitmap) -----------------------------

def coverage_at(a: Sequence[int], b: Sequence[int], offset: int, p: DetectParams) -> bytearray:
    """Bitmap over A marking which items actually match B at this alignment."""
    cov = bytearray(len(a))
    mask, max_bit_err = p.mask, p.max_bit_err
    nb = len(b)
    for i, va in enumerate(a):
        j = i + offset
        if 0 <= j < nb and ((va ^ b[j]) & mask).bit_count() <= max_bit_err:
            cov[i] = 1
    return cov


def match_cover(a: Sequence[int], b: Sequence[int], p: DetectParams) -> bytearray:
    """Union the coverage bitmaps over every candidate offset, so all shared
    segments between the pair are captured in one pass."""
    cov = bytearray(len(a))
    for off in candidate_offsets(a, b, p):
        part = coverage_at(a, b, off, p)
        for i, c in enumerate(part):
            if c:
                cov[i] = 1
    return cov


# --- §6  From recurring items to timestamped segments ------------------------

def _runs(cover: Sequence[int], min_len: int, max_gap: int, min_density: float) -> list[tuple[int, int]]:
    """Turn a noisy coverage bitmap into clean (start, end) item ranges.

    Bridges short gaps, requires a run be *mostly* covered (density), and
    enforces a minimum length — the three guards that separate real segments
    from coincidental specks.
    """
    runs: list[tuple[int, int]] = []
    n = len(cover)
    p = 0
    while p < n:
        if not cover[p]:
            p += 1
            continue
        q = p
        last = p
        gap = 0
        count = 0
        while q < n:
            if cover[q]:
                last = q
                gap = 0
                count += 1
            else:
                gap += 1
                if gap > max_gap:
                    break
            q += 1
        length = last + 1 - p
        if length >= min_len and count / length >= min_density:
            runs.append((p, last + 1))
        p = q + 1
    return runs


def _merge(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Merge overlapping / touching second-ranges."""
    if not ranges:
        return []
    ranges = sorted(ranges)
    out = [ranges[0]]
    for s, e in ranges[1:]:
        ls, le = out[-1]
        if s <= le:
            out[-1] = (ls, max(le, e))
        else:
            out.append((s, e))
    return out


# --- §5 + §6  Cross-file recurrence → per-file timestamped segments ----------

def recurring_segments(
    fingerprints: Sequence[Fingerprint],
    p: DetectParams,
) -> list[list[tuple[float, float]]]:
    """For each input file, the list of (start, end) second-ranges that recur
    across the set (present in at least ``min_shows`` files total).
    """
    n = len(fingerprints)
    results: list[list[tuple[float, float]]] = []
    for i in range(n):
        a = fingerprints[i].items
        item_sec = fingerprints[i].item_sec
        match_count = [0] * len(a)
        for j in range(n):
            if j == i:
                continue
            cov = match_cover(a, fingerprints[j].items, p)
            for pos in range(len(a)):
                match_count[pos] += cov[pos]

        recurring = [c >= (p.min_shows - 1) for c in match_count]
        min_len = max(1, round(p.min_seconds / item_sec))
        max_gap = max(0, round(p.max_gap_seconds / item_sec))
        runs = _runs(recurring, min_len, max_gap, p.min_density)
        seconds = _merge([(s * item_sec, e * item_sec) for s, e in runs])
        results.append(seconds)
    return results


# --- §7  Matching a *known* pattern against a new file -----------------------

def locate(pattern: Sequence[int], b: Sequence[int], p: DetectParams) -> tuple[int, int] | None:
    """Where does ``pattern`` occur in fingerprint ``b``? Returns the (start,
    end) item indices in B, or None if the pattern doesn't align well enough.
    """
    if not pattern or not b:
        return None
    best_cov: bytearray | None = None
    best_off = 0
    best_n = 0
    for off in candidate_offsets(pattern, b, p):
        cov = coverage_at(pattern, b, off, p)
        n = sum(cov)
        if n > best_n:
            best_n, best_cov, best_off = n, cov, off
    if best_cov is None or best_n / len(pattern) < p.locate_min_ratio:
        return None
    hits = [i + best_off for i, c in enumerate(best_cov) if c]
    return max(0, min(hits)), min(len(b), max(hits) + 1)


def best_ratio(pattern: Sequence[int], b: Sequence[int], p: DetectParams) -> float:
    """The fraction of ``pattern`` that aligns to ``b`` at its best offset.

    Used to de-duplicate stored patterns: two patterns are "the same" when this
    exceeds ``dedupe_ratio`` (§7), so re-detecting an intro increments one
    pattern's count instead of spawning duplicates.
    """
    if not pattern or not b:
        return 0.0
    best_n = 0
    for off in candidate_offsets(pattern, b, p):
        n = sum(coverage_at(pattern, b, off, p))
        if n > best_n:
            best_n = n
    return best_n / len(pattern)

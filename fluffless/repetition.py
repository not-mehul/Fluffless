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

from array import array
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence

# Verification reaches this many items beyond the outermost voting position, to
# catch a segment's edges where items Hamming-match but didn't seed cleanly.
SEG_MARGIN = 48

# Below this many files, the cost of spinning up worker processes outweighs the
# parallel speed-up, so detection stays single-process regardless of --workers.
PARALLEL_MIN_FILES = 6


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
    seed_max_bucket: int = 48   # skip seed keys shared by more items than this
    # ^ silence / repetitive content makes one masked key collect thousands of
    #   items; voting on it is O(L²) and finds nothing useful, so it is skipped.
    seed_min_votes: int = 3     # an offset needs this many votes to be verified
    # ^ a real shared segment produces many votes; random offsets get 0–2.
    #   Skipping the low-vote offsets avoids walking the whole file for
    #   alignments that cannot contain a segment — the main speed dial. Kept low
    #   so heavily re-encoded short segments survive; the density/min-length
    #   guards in run extraction remain the real false-positive defence.
    seed_max_spread: int = 40   # max item-span per vote for an offset to verify
    # ^ a real segment's votes cluster (one every few items); a noise offset's
    #   few votes scatter across the whole file. Skipping wide-but-sparse
    #   offsets avoids verifying enormous windows that hold no real segment.

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
            seed_max_bucket=self.seed_max_bucket,
            seed_min_votes=self.seed_min_votes,
            seed_max_spread=self.seed_max_spread,
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
    return [off for off, _, _ in _ranged_offsets(a, _seed_index(b, p.key_mask), p)]


def _seed_index(items: Sequence[int], key_mask: int) -> dict[int, list[int]]:
    """Bucket items by their masked key. Built once per file and reused across
    every pair that file takes part in (see :func:`recurring_segments`)."""
    index: dict[int, list[int]] = defaultdict(list)
    for j, v in enumerate(items):
        index[v & key_mask].append(j)
    return index


def _ranged_offsets(
    a: Sequence[int], index: dict[int, list[int]], p: DetectParams,
) -> list[tuple[int, int, int]]:
    """Vote for offsets using a prebuilt key→positions index of B, returning
    each strong offset together with the span of A-positions that voted for it.

    That span localises the shared segment, so the caller can verify just a
    window around it instead of walking the whole file — the difference between
    a few-second scan and many minutes on a large library.

    Keys shared by more than ``seed_max_bucket`` items (silence, tones, static
    frames) are skipped: they are non-discriminative and voting on them is
    quadratic, which is what makes a large library appear to hang.
    """
    key_mask = p.key_mask
    max_bucket = p.seed_max_bucket
    acc: dict[int, list[int]] = {}          # offset -> [count, min_i, max_i]
    get = index.get
    aget = acc.get
    for i, v in enumerate(a):
        bucket = get(v & key_mask)
        if not bucket or len(bucket) > max_bucket:
            continue
        for j in bucket:
            d = j - i
            e = aget(d)
            if e is None:
                acc[d] = [1, i, i]
            else:
                e[0] += 1
                if i < e[1]:
                    e[1] = i
                elif i > e[2]:
                    e[2] = i
    # Keep offsets that are both well-voted and *clustered*: enough votes, and
    # a span consistent with those votes (a real segment keys roughly one item
    # in ten; noise scatters its few votes across the file). This filtering
    # happens before any verification, so wide sparse offsets cost nothing.
    min_votes = p.seed_min_votes
    max_spread = p.seed_max_spread
    strong = [
        (c, off, lo, hi)
        for off, (c, lo, hi) in acc.items()
        if c >= min_votes and (hi - lo) <= c * max_spread
    ]
    if len(strong) > p.top_offsets:
        strong.sort(reverse=True)
        strong = strong[:p.top_offsets]
    return [(off, lo, hi) for _, off, lo, hi in strong]


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

def _pair_runs(
    ai, lai: int, bj, lbj: int, index_j: dict[int, list[int]],
    mlen: int, mgap: int, p: DetectParams,
) -> tuple[set[int], set[int]] | None:
    """For one file pair, the positions where the two share a *dense,
    minimum-length run* — returned as (positions-in-A, positions-in-B), or None.

    This per-pair density guard is what stops coincidental matches from
    accumulating into phantom segments at a scale of hundreds of files. Shared
    by the serial and parallel detection paths so they stay identical.
    """
    offsets = _ranged_offsets(ai, index_j, p)
    if not offsets:
        return None
    mask = p.mask
    max_bit_err = p.max_bit_err
    min_density = p.min_density
    runs_i: set[int] = set()
    runs_j: set[int] = set()
    for off, vlo, vhi in offsets:
        # Verify only a window around the voting positions — the shared segment
        # lives there — instead of walking the whole file.
        lo = vlo - SEG_MARGIN
        if lo < 0:
            lo = 0
        if off < 0 and lo < -off:
            lo = -off
        hi = vhi + 1 + SEG_MARGIN
        if hi > lai:
            hi = lai
        if hi > lbj - off:
            hi = lbj - off
        if hi - lo < mlen:
            continue
        seg = bytearray(hi - lo)
        ipos = lo
        while ipos < hi:
            if ((ai[ipos] ^ bj[ipos + off]) & mask).bit_count() <= max_bit_err:
                seg[ipos - lo] = 1
            ipos += 1
        for rs, re in _runs(seg, mlen, mgap, min_density):
            for pos in range(lo + rs, lo + re):
                runs_i.add(pos)
                runs_j.add(pos + off)
    return (runs_i, runs_j) if runs_i else None


def _extract(count: "array | dict[int, int]", L: int, threshold: int,
             mlen: int, mgap: int, min_density: float, item_sec: float
             ) -> list[tuple[float, float]]:
    """Turn per-position recurrence counts into merged second-ranges."""
    recurring = bytearray(L)
    if isinstance(count, dict):
        for pos, c in count.items():
            if c >= threshold:
                recurring[pos] = 1
    else:
        for pos, c in enumerate(count):
            if c >= threshold:
                recurring[pos] = 1
    runs = _runs(recurring, mlen, mgap, min_density)
    return _merge([(s * item_sec, e * item_sec) for s, e in runs])


def recurring_segments(
    fingerprints: Sequence[Fingerprint],
    p: DetectParams,
    on_progress: "Callable[[int, int], None] | None" = None,
    workers: int = 1,
) -> list[list[tuple[float, float]]]:
    """For each input file, the list of (start, end) second-ranges that recur
    across the set (present in at least ``min_shows`` files total).

    Each unordered pair is aligned and verified once; the match credits both
    files' per-position recurrence counts. ``on_progress(done_pairs,
    total_pairs)`` reports progress. With ``workers > 1`` the O(N²) pair work is
    spread across processes (it is pure-Python CPU work, so threads wouldn't
    help); any failure falls back to the single-process path automatically.
    """
    n = len(fingerprints)
    if n == 0:
        return []
    if workers and workers > 1 and n >= PARALLEL_MIN_FILES:
        try:
            return _recurring_parallel(fingerprints, p, on_progress, workers)
        except Exception:  # noqa: BLE001 — never let parallelism break a scan
            pass
    return _recurring_serial(fingerprints, p, on_progress)


def _recurring_serial(
    fingerprints: Sequence[Fingerprint],
    p: DetectParams,
    on_progress: "Callable[[int, int], None] | None",
) -> list[list[tuple[float, float]]]:
    n = len(fingerprints)
    items = [fp.items for fp in fingerprints]
    lens = [len(x) for x in items]
    counts = [array("H", bytes(2 * L)) for L in lens]
    min_lens = [max(1, round(p.min_seconds / fp.item_sec)) for fp in fingerprints]
    max_gaps = [max(0, round(p.max_gap_seconds / fp.item_sec)) for fp in fingerprints]
    total_pairs = n * (n - 1) // 2
    done = 0

    for j in range(1, n):
        index_j = _seed_index(items[j], p.key_mask)
        lbj = lens[j]
        cj = counts[j]
        for i in range(j):
            res = _pair_runs(items[i], lens[i], items[j], lbj, index_j,
                             min_lens[i], max_gaps[i], p)
            if res:
                ri, rj = res
                ci = counts[i]
                for ip in ri:
                    ci[ip] += 1
                for jp in rj:
                    cj[jp] += 1
            done += 1
        if on_progress:
            on_progress(done, total_pairs)

    threshold = p.min_shows - 1
    return [
        _extract(counts[i], lens[i], threshold, min_lens[i], max_gaps[i],
                 p.min_density, fingerprints[i].item_sec)
        for i in range(n)
    ]


# --- parallel detection (process pool) ---------------------------------------
# Each worker process holds the fingerprint set in module globals (loaded once
# via the initializer), so per-task payloads are just a file index.
_PW_ITEMS: list | None = None
_PW_LENS: list[int] | None = None
_PW_MINLEN: list[int] | None = None
_PW_MAXGAP: list[int] | None = None
_PW_PARAMS: DetectParams | None = None


def _pw_init(items, lens, min_lens, max_gaps, params) -> None:
    global _PW_ITEMS, _PW_LENS, _PW_MINLEN, _PW_MAXGAP, _PW_PARAMS
    _PW_ITEMS, _PW_LENS = items, lens
    _PW_MINLEN, _PW_MAXGAP, _PW_PARAMS = min_lens, max_gaps, params


def _pw_pairs_for_j(j: int) -> dict[int, dict[int, int]]:
    """All pairs (i, j) for i < j, as sparse per-file position counts. Both
    files of each matching pair are credited, so the merge of all j reproduces
    the serial accumulation exactly."""
    items, lens, p = _PW_ITEMS, _PW_LENS, _PW_PARAMS
    index_j = _seed_index(items[j], p.key_mask)
    lbj = lens[j]
    local: dict[int, dict[int, int]] = {}
    for i in range(j):
        res = _pair_runs(items[i], lens[i], items[j], lbj, index_j,
                         _PW_MINLEN[i], _PW_MAXGAP[i], p)
        if not res:
            continue
        ri, rj = res
        di = local.setdefault(i, {})
        for pos in ri:
            di[pos] = di.get(pos, 0) + 1
        dj = local.setdefault(j, {})
        for pos in rj:
            dj[pos] = dj.get(pos, 0) + 1
    return local


def _recurring_parallel(
    fingerprints: Sequence[Fingerprint],
    p: DetectParams,
    on_progress: "Callable[[int, int], None] | None",
    workers: int,
) -> list[list[tuple[float, float]]]:
    n = len(fingerprints)
    # array('Q') keeps the fingerprints compact (8 bytes/item) so they pickle to
    # the workers cheaply and don't balloon memory under spawn.
    items = [array("Q", fp.items) for fp in fingerprints]
    lens = [len(x) for x in items]
    min_lens = [max(1, round(p.min_seconds / fp.item_sec)) for fp in fingerprints]
    max_gaps = [max(0, round(p.max_gap_seconds / fp.item_sec)) for fp in fingerprints]

    totals: dict[int, dict[int, int]] = {}
    total_pairs = n * (n - 1) // 2
    done = 0
    with ProcessPoolExecutor(
        max_workers=workers, initializer=_pw_init,
        initargs=(items, lens, min_lens, max_gaps, p),
    ) as ex:
        futures = {ex.submit(_pw_pairs_for_j, j): j for j in range(1, n)}
        for fut in as_completed(futures):
            j = futures[fut]
            for f, d in fut.result().items():
                td = totals.get(f)
                if td is None:
                    totals[f] = dict(d)
                else:
                    for pos, c in d.items():
                        td[pos] = td.get(pos, 0) + c
            done += j
            if on_progress:
                on_progress(done, total_pairs)

    threshold = p.min_shows - 1
    return [
        _extract(totals.get(i, {}), lens[i], threshold, min_lens[i], max_gaps[i],
                 p.min_density, fingerprints[i].item_sec)
        for i in range(n)
    ]


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


def locate_all(pattern: Sequence[int], b: Sequence[int], p: DetectParams) -> list[tuple[int, int]]:
    """Every place ``pattern`` occurs in fingerprint ``b`` — not just the best
    one. Returns a sorted list of non-overlapping (start, end) item ranges.

    The same ad can air more than once in a single episode (mid-rolls, repeated
    sponsor reads); each airing aligns at its own offset, so we evaluate every
    candidate offset, keep those that cover enough of the pattern, and greedily
    accept the strongest spans that don't overlap one already taken. This is
    what lets a confirmed segment be cut from *all* its occurrences, not one.
    """
    if not pattern or not b:
        return []
    plen = len(pattern)
    nb = len(b)
    spans: list[tuple[int, int, int]] = []   # (coverage, start, end)
    for off in candidate_offsets(pattern, b, p):
        cov = coverage_at(pattern, b, off, p)
        n = sum(cov)
        if n / plen < p.locate_min_ratio:
            continue
        hits = [i + off for i, c in enumerate(cov) if c]
        if not hits:
            continue
        s = max(0, min(hits))
        e = min(nb, max(hits) + 1)
        if e > s:
            spans.append((n, s, e))
    # Strongest first, then greedily keep spans that don't overlap a taken one,
    # so two airings of the same ad are both reported but a single airing seen
    # at two near-identical offsets collapses to one.
    spans.sort(reverse=True)
    chosen: list[tuple[int, int]] = []
    for _n, s, e in spans:
        if all(e <= cs or s >= ce for cs, ce in chosen):
            chosen.append((s, e))
    chosen.sort()
    return chosen



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

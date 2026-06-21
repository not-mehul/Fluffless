"""Local-first storage for Fluffless.

Everything the tool learns lives on-device in a SQLite file inside the library
(``<library>/.fluffless/fluffless.db``). Three record types:

  * **patterns** — a stored fingerprint slice + review status (pending until the
    user decides; confirmed once marked an ad and approved for removal; or
    dismissed), the durable knowledge that survives across runs.
  * **clips**    — a concrete occurrence of a pattern in one file, with its
    timestamps and an extracted preview, so it can be played back in-tool.
  * **processed** — which files the "Remove the Fluff" step has already run on,
    so a folder can be reused: add more files, re-scan, keep the history.

The whole database is exportable (JSON for round-tripping, Markdown for an
inspectable, ownable backup).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from array import array
from dataclasses import dataclass

from .repetition import Fingerprint

# Review lifecycle of a detected segment. Detection only proposes (``pending``);
# the user decides. Marking something an ad ``confirmed``s it and approves it for
# removal; ``dismissed`` segments are kept (so they aren't re-proposed) but never
# cut. This replaces the old Ad/Intro/Outro/Other taxonomy entirely.
STATUSES = ("pending", "confirmed", "dismissed")
WORKSPACE = ".fluffless"
DB_NAME = "fluffless.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS patterns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    library     TEXT NOT NULL,
    folder      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | confirmed | dismissed
    bits        INTEGER NOT NULL,
    item_sec    REAL NOT NULL,
    items       TEXT NOT NULL,          -- JSON array of fingerprint integers (current/effective base)
    duration    REAL NOT NULL,
    shows       INTEGER NOT NULL DEFAULT 1,
    head_items  INTEGER NOT NULL DEFAULT 0,  -- items trimmed off the front (refinement)
    tail_items  INTEGER NOT NULL DEFAULT 0,  -- items trimmed off the end (refinement)
    orig_items  TEXT,                    -- detected fingerprint baseline (for "reset to default")
    orig_duration REAL,                  -- detected duration baseline (for "reset to default")
    pinned      INTEGER NOT NULL DEFAULT 0,  -- 1 once the user fixes the fingerprint by hand
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS clips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id  INTEGER NOT NULL REFERENCES patterns(id) ON DELETE CASCADE,
    file_path   TEXT NOT NULL,
    file_name   TEXT NOT NULL,
    start       REAL NOT NULL,
    end         REAL NOT NULL,
    orig_start  REAL,                   -- detected bounds, never edited (for refinement)
    orig_end    REAL,
    preview     TEXT,                   -- path to extracted preview clip
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS processed (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path   TEXT NOT NULL,
    file_name   TEXT NOT NULL,
    output_path TEXT,
    removed     TEXT,                   -- JSON: list of removed [start,end]
    saved_sec   REAL NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS fingerprints (
    file_path   TEXT PRIMARY KEY,       -- one cached fingerprint per scanned file
    folder      TEXT NOT NULL,
    bits        INTEGER NOT NULL,
    item_sec    REAL NOT NULL,
    items       BLOB NOT NULL,          -- packed array of fingerprint integers
    created_at  REAL NOT NULL
);
"""


def _now() -> float:
    return time.time()


@dataclass
class Database:
    path: str
    conn: sqlite3.Connection

    @classmethod
    def open(cls, library: str) -> "Database":
        ws = os.path.join(os.path.abspath(os.path.expanduser(library)), WORKSPACE)
        os.makedirs(ws, exist_ok=True)
        path = os.path.join(ws, DB_NAME)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        cls._migrate(conn)
        conn.commit()
        return cls(path=path, conn=conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Bring an existing database up to the current schema in place, so a
        user's catalogue survives upgrades without a re-scan."""
        def cols(table: str) -> set[str]:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}

        ccols = cols("clips")
        if "orig_start" not in ccols:
            conn.execute("ALTER TABLE clips ADD COLUMN orig_start REAL")
            conn.execute("ALTER TABLE clips ADD COLUMN orig_end REAL")
            # Backfill: existing clips' current bounds are their detected bounds.
            conn.execute("UPDATE clips SET orig_start = start, orig_end = end "
                         "WHERE orig_start IS NULL")
        pcols = cols("patterns")
        if "head_items" not in pcols:
            conn.execute("ALTER TABLE patterns ADD COLUMN head_items INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE patterns ADD COLUMN tail_items INTEGER NOT NULL DEFAULT 0")
        if "orig_items" not in pcols:
            conn.execute("ALTER TABLE patterns ADD COLUMN orig_items TEXT")
            conn.execute("ALTER TABLE patterns ADD COLUMN orig_duration REAL")
            # Backfill: an existing pattern's current fingerprint is its baseline.
            conn.execute("UPDATE patterns SET orig_items = items, orig_duration = duration "
                         "WHERE orig_items IS NULL")
        if "pinned" not in pcols:
            conn.execute("ALTER TABLE patterns ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        if "status" not in pcols:
            conn.execute("ALTER TABLE patterns ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
            # Carry the old taxonomy forward: a segment previously identified as
            # an "Ad" was, in effect, confirmed for removal; anything else was
            # never approved, so it returns to the review queue as pending.
            if "label" in pcols:
                conn.execute("UPDATE patterns SET status = 'confirmed' WHERE label = 'Ad'")

    @property
    def workspace(self) -> str:
        return os.path.dirname(self.path)

    # --- patterns ------------------------------------------------------------

    def add_pattern(
        self, library: str, folder: str, items: list[int], item_sec: float,
        bits: int, duration: float, status: str = "pending",
    ) -> int:
        now = _now()
        blob = json.dumps(items)
        cur = self.conn.execute(
            "INSERT INTO patterns (library, folder, status, bits, item_sec, items, "
            "duration, shows, orig_items, orig_duration, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (library, folder, status, bits, item_sec, blob, duration, 1, blob, duration, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def bump_pattern(self, pattern_id: int) -> None:
        self.conn.execute(
            "UPDATE patterns SET shows = shows + 1, updated_at = ? WHERE id = ?",
            (_now(), pattern_id),
        )
        self.conn.commit()

    def set_status(self, pattern_id: int, status: str) -> None:
        """Record the user's review decision for a segment. ``confirmed`` means
        "this is an ad — approved for removal"; ``dismissed`` means "not an ad,
        leave it"; ``pending`` returns it to the undecided queue."""
        if status not in STATUSES:
            raise ValueError(f"invalid status: {status}")
        self.conn.execute(
            "UPDATE patterns SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), pattern_id),
        )
        self.conn.commit()

    def delete_pattern(self, pattern_id: int) -> None:
        self.conn.execute("DELETE FROM patterns WHERE id = ?", (pattern_id,))
        self.conn.commit()

    def patterns(self, folder: str | None = None) -> list[sqlite3.Row]:
        if folder is None:
            return list(self.conn.execute("SELECT * FROM patterns ORDER BY id"))
        return list(self.conn.execute(
            "SELECT * FROM patterns WHERE folder = ? ORDER BY id", (folder,)
        ))

    def pattern(self, pattern_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM patterns WHERE id = ?", (pattern_id,)
        ).fetchone()

    def pattern_items(self, row: sqlite3.Row) -> list[int]:
        """The *effective* fingerprint used for matching: the original items
        with any refinement (head/tail trim) applied. Tightening a pattern
        therefore tightens what future scans locate, with no re-detection."""
        items = json.loads(row["items"])
        head = row["head_items"] if "head_items" in row.keys() else 0
        tail = row["tail_items"] if "tail_items" in row.keys() else 0
        if head or tail:
            end = len(items) - tail
            if end > head:
                return items[head:end]
        return items

    # --- clips ---------------------------------------------------------------

    def add_clip(
        self, pattern_id: int, file_path: str, start: float, end: float,
        preview: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO clips (pattern_id, file_path, file_name, start, end, "
            "orig_start, orig_end, preview, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (pattern_id, file_path, os.path.basename(file_path), start, end,
             start, end, preview, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def clip_exists(self, pattern_id: int, file_path: str, start: float) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM clips WHERE pattern_id = ? AND file_path = ? "
            "AND ABS(start - ?) < 1.0 LIMIT 1",
            (pattern_id, file_path, start),
        ).fetchone()
        return row is not None

    def set_clip_preview(self, clip_id: int, preview: str) -> None:
        self.conn.execute("UPDATE clips SET preview = ? WHERE id = ?", (preview, clip_id))
        self.conn.commit()

    def update_clip_bounds(self, clip_id: int, start: float, end: float) -> None:
        """Set a clip's exact start/end and clear its (now stale) preview."""
        self.conn.execute(
            "UPDATE clips SET start = ?, end = ?, preview = NULL WHERE id = ?",
            (start, end, clip_id),
        )
        self.conn.commit()

    def set_clip_detected(self, clip_id: int, start: float, end: float) -> None:
        """Set a clip's *detected* bounds (start/end and the immutable orig_*),
        used when normalising a freshly-detected clip to the canonical length."""
        self.conn.execute(
            "UPDATE clips SET start = ?, end = ?, orig_start = ?, orig_end = ?, "
            "preview = NULL WHERE id = ?",
            (start, end, start, end, clip_id),
        )
        self.conn.commit()

    def set_pattern_fingerprint(self, pattern_id: int, items: list[int], duration: float) -> None:
        """Replace a pattern's canonical fingerprint *and* its reset baseline,
        clearing any refinement. Used by scan-time length normalisation — this is
        what "default" means, so reset-to-default returns here."""
        blob = json.dumps(items)
        self.conn.execute(
            "UPDATE patterns SET items = ?, orig_items = ?, duration = ?, "
            "orig_duration = ?, head_items = 0, tail_items = 0, updated_at = ? WHERE id = ?",
            (blob, blob, duration, duration, _now(), pattern_id),
        )
        self.conn.commit()

    def pin_fingerprint(self, pattern_id: int, items: list[int], duration: float) -> None:
        """Adopt a user-chosen fingerprint as the pattern's canonical one without
        touching the reset baseline, and mark the pattern *pinned* so a later
        re-scan won't re-normalise it away. This is the mechanism behind
        "use this clip's cropped region as the saved fingerprint"."""
        self.conn.execute(
            "UPDATE patterns SET items = ?, duration = ?, head_items = 0, "
            "tail_items = 0, pinned = 1, updated_at = ? WHERE id = ?",
            (json.dumps(items), duration, _now(), pattern_id),
        )
        self.conn.commit()

    def reset_clip(self, clip_id: int) -> tuple[float, float] | None:
        """Return one clip to its detected bounds (clearing its stale preview).
        Leaves the pattern fingerprint untouched."""
        clip = self.clip(clip_id)
        if clip is None:
            return None
        os_, oe = self._orig_bounds(clip)
        self.conn.execute(
            "UPDATE clips SET start = ?, end = ?, preview = NULL WHERE id = ?",
            (os_, oe, clip_id),
        )
        self.conn.commit()
        return (os_, oe)

    def _recount_shows(self, pattern_id: int) -> int:
        """Set a pattern's ``shows`` to its current clip count; delete it if it
        has no clips left. Returns the new count (0 ⇒ deleted)."""
        n = self.conn.execute(
            "SELECT COUNT(*) FROM clips WHERE pattern_id = ?", (pattern_id,)
        ).fetchone()[0]
        if n == 0:
            self.conn.execute("DELETE FROM patterns WHERE id = ?", (pattern_id,))
        else:
            self.conn.execute(
                "UPDATE patterns SET shows = ?, updated_at = ? WHERE id = ?",
                (n, _now(), pattern_id),
            )
        return n

    def recount_shows(self, pattern_id: int) -> int:
        """Public wrapper: set a pattern's ``shows`` to its live clip count (and
        delete it if it has none), committing the change. Returns the new count."""
        n = self._recount_shows(pattern_id)
        self.conn.commit()
        return n

    def move_clip(self, clip_id: int, target_pattern_id: int) -> dict | None:
        """Reassign one clip to another existing pattern in the same folder —
        correcting a mis-grouping. Recounts both patterns and removes the source
        if it is left empty."""
        clip = self.clip(clip_id)
        target = self.pattern(target_pattern_id)
        if clip is None or target is None:
            return None
        src_id = clip["pattern_id"]
        if src_id == target_pattern_id:
            return {"moved": False, "to": target_pattern_id,
                    "deleted_source": False, "folder": target["folder"]}
        self.conn.execute(
            "UPDATE clips SET pattern_id = ? WHERE id = ?", (target_pattern_id, clip_id)
        )
        self._recount_shows(target_pattern_id)
        deleted = self._recount_shows(src_id) == 0
        self.conn.commit()
        return {"moved": True, "from": src_id, "to": target_pattern_id,
                "deleted_source": deleted, "folder": target["folder"]}

    def new_group_from_clip(self, clip_id: int, status: str = "pending") -> dict | None:
        """Split one clip out into a brand-new pattern whose fingerprint is the
        clip's own cropped region — for when a clip was grouped with the wrong
        segment. Removes the source pattern if it is left empty."""
        clip = self.clip(clip_id)
        if clip is None:
            return None
        src = self.pattern(clip["pattern_id"])
        if src is None:
            return None
        fp = self.get_fingerprint(clip["file_path"])
        if fp is None:
            return {"error": "no cached fingerprint for this file — re-scan the folder first"}
        items = list(fp.slice_seconds(clip["start"], clip["end"]))
        if len(items) < 1:
            return {"error": "selection is too short to fingerprint"}
        dur = max(fp.item_sec, clip["end"] - clip["start"])
        new_id = self.add_pattern(
            src["library"], src["folder"], items, fp.item_sec, fp.bits, dur, status,
        )
        self.conn.execute("UPDATE patterns SET pinned = 1 WHERE id = ?", (new_id,))
        self.conn.execute("UPDATE clips SET pattern_id = ? WHERE id = ?", (new_id, clip_id))
        self._recount_shows(new_id)
        deleted = self._recount_shows(clip["pattern_id"]) == 0
        self.conn.commit()
        return {"new_pattern_id": new_id, "from": clip["pattern_id"],
                "deleted_source": deleted, "folder": src["folder"]}

    @staticmethod
    def _orig_bounds(clip: sqlite3.Row) -> tuple[float, float]:
        """A clip's detected bounds, falling back to current bounds for rows
        created before the orig columns existed."""
        os_ = clip["orig_start"] if clip["orig_start"] is not None else clip["start"]
        oe = clip["orig_end"] if clip["orig_end"] is not None else clip["end"]
        return os_, oe

    def clips(self, pattern_id: int | None = None) -> list[sqlite3.Row]:
        if pattern_id is None:
            return list(self.conn.execute("SELECT * FROM clips ORDER BY id"))
        return list(self.conn.execute(
            "SELECT * FROM clips WHERE pattern_id = ? ORDER BY start", (pattern_id,)
        ))

    def clip(self, clip_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()

    def delete_clip(self, clip_id: int) -> None:
        self.conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
        self.conn.commit()

    # --- processed -----------------------------------------------------------

    def add_processed(
        self, file_path: str, output_path: str, removed: list[dict], saved_sec: float,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO processed (file_path, file_name, output_path, removed, "
            "saved_sec, created_at) VALUES (?,?,?,?,?,?)",
            (file_path, os.path.basename(file_path), output_path,
             json.dumps(removed), saved_sec, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def is_processed(self, file_path: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM processed WHERE file_path = ? LIMIT 1", (file_path,)
        ).fetchone()
        return row is not None

    def processed(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM processed ORDER BY id DESC"))

    # --- cached fingerprints -------------------------------------------------
    # Each scanned file's fingerprint is kept so a newly-identified ad can be
    # checked against every file (old ones included) without re-reading audio.

    def store_fingerprint(self, file_path: str, folder: str, fp: Fingerprint) -> None:
        typecode = "Q" if fp.bits > 32 else "I"
        blob = array(typecode, fp.items).tobytes()
        self.conn.execute(
            "INSERT OR REPLACE INTO fingerprints (file_path, folder, bits, "
            "item_sec, items, created_at) VALUES (?,?,?,?,?,?)",
            (file_path, folder, fp.bits, fp.item_sec, blob, _now()),
        )
        self.conn.commit()

    def fingerprints(self, folder: str) -> list[tuple[str, Fingerprint]]:
        out = []
        for row in self.conn.execute(
            "SELECT * FROM fingerprints WHERE folder = ?", (folder,)
        ):
            typecode = "Q" if row["bits"] > 32 else "I"
            arr = array(typecode)
            arr.frombytes(row["items"])
            out.append((row["file_path"],
                        Fingerprint(items=arr, item_sec=row["item_sec"], bits=row["bits"])))
        return out

    def get_fingerprint(self, file_path: str) -> Fingerprint | None:
        """The one cached fingerprint for a file, or None if it was never
        scanned (or its cache was cleared)."""
        row = self.conn.execute(
            "SELECT * FROM fingerprints WHERE file_path = ?", (file_path,)
        ).fetchone()
        if row is None:
            return None
        typecode = "Q" if row["bits"] > 32 else "I"
        arr = array(typecode)
        arr.frombytes(row["items"])
        return Fingerprint(items=arr, item_sec=row["item_sec"], bits=row["bits"])

    def has_fingerprint(self, file_path: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM fingerprints WHERE file_path = ? LIMIT 1", (file_path,)
        ).fetchone() is not None

    # --- export --------------------------------------------------------------

    def export_dict(self) -> dict:
        def rows(table: str) -> list[dict]:
            return [dict(r) for r in self.conn.execute(f"SELECT * FROM {table} ORDER BY id")]
        return {
            "fluffless": 1,
            "exported_at": _now(),
            "patterns": rows("patterns"),
            "clips": rows("clips"),
            "processed": rows("processed"),
        }

    def export_json(self) -> str:
        return json.dumps(self.export_dict(), indent=2)

    def export_markdown(self) -> str:
        data = self.export_dict()
        lines = ["# Fluffless database export", ""]
        lines.append(f"_{len(data['patterns'])} patterns · "
                     f"{len(data['clips'])} clips · "
                     f"{len(data['processed'])} processed files_")
        lines.append("")
        lines.append("## Patterns")
        lines.append("")
        if not data["patterns"]:
            lines.append("_No patterns catalogued yet._")
        for p in data["patterns"]:
            lines.append(
                f"- **{p.get('status', 'pending')}** · {p['folder']} · "
                f"{_fmt(p['duration'])} · seen in {p['shows']} file(s) "
                f"`#{p['id']}`"
            )
        lines.append("")
        lines.append("## Removed segments")
        lines.append("")
        if not data["processed"]:
            lines.append("_Nothing trimmed yet._")
        for r in data["processed"]:
            removed = json.loads(r["removed"] or "[]")
            lines.append(f"- `{r['file_name']}` — saved {_fmt(r['saved_sec'])}")
            for seg in removed:
                lines.append(
                    f"  - {_fmt(seg.get('start',0))} → {_fmt(seg.get('end',0))}"
                )
        lines.append("")
        return "\n".join(lines)

    def close(self) -> None:
        self.conn.close()


def _fmt(seconds: float) -> str:
    seconds = float(seconds or 0)
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

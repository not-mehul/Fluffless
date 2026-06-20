"""Local-first storage for Fluffless.

Everything the tool learns lives on-device in a SQLite file inside the library
(``<library>/.fluffless/fluffless.db``). Three record types:

  * **patterns** — a stored fingerprint slice + label (Ad/Intro/Outro/Other),
    the durable knowledge that survives across runs.
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
from dataclasses import dataclass

LABELS = ("Ad", "Intro", "Outro", "Other")
WORKSPACE = ".fluffless"
DB_NAME = "fluffless.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS patterns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    library     TEXT NOT NULL,
    folder      TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT 'Other',
    bits        INTEGER NOT NULL,
    item_sec    REAL NOT NULL,
    items       TEXT NOT NULL,          -- JSON array of fingerprint integers (original)
    duration    REAL NOT NULL,
    shows       INTEGER NOT NULL DEFAULT 1,
    head_items  INTEGER NOT NULL DEFAULT 0,  -- items trimmed off the front (refinement)
    tail_items  INTEGER NOT NULL DEFAULT 0,  -- items trimmed off the end (refinement)
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
    removed     TEXT,                   -- JSON: list of removed [start,end,label]
    saved_sec   REAL NOT NULL DEFAULT 0,
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

    @property
    def workspace(self) -> str:
        return os.path.dirname(self.path)

    # --- patterns ------------------------------------------------------------

    def add_pattern(
        self, library: str, folder: str, items: list[int], item_sec: float,
        bits: int, duration: float, label: str = "Other",
    ) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO patterns (library, folder, label, bits, item_sec, items, "
            "duration, shows, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (library, folder, label, bits, item_sec, json.dumps(items), duration, 1, now, now),
        )
        self.conn.commit()
        return cur.lastrowid

    def bump_pattern(self, pattern_id: int) -> None:
        self.conn.execute(
            "UPDATE patterns SET shows = shows + 1, updated_at = ? WHERE id = ?",
            (_now(), pattern_id),
        )
        self.conn.commit()

    def set_label(self, pattern_id: int, label: str) -> None:
        if label not in LABELS:
            raise ValueError(f"invalid label: {label}")
        self.conn.execute(
            "UPDATE patterns SET label = ?, updated_at = ? WHERE id = ?",
            (label, _now(), pattern_id),
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

    def trim_pattern(self, pattern_id: int, head: float, tail: float) -> int | None:
        """Set a pattern's refinement to ``head``/``tail`` seconds: tighten the
        stored fingerprint (so future scans locate just this, not the
        surrounding content) and move every clip's bounds to its *original*
        detected start/end shifted inward by the same amounts.

        This is **absolute and idempotent** — applying the same trim twice is a
        no-op, and it is the operation behind both the manual trim panel and
        "apply one refined clip to all" (see :meth:`propagate_from_clip`).
        Returns the number of clips adjusted, or None if the pattern is unknown.
        """
        row = self.pattern(pattern_id)
        if row is None:
            return None
        head = max(0.0, head)
        tail = max(0.0, tail)
        item_sec = row["item_sec"] or 0.1238
        n_items = len(json.loads(row["items"]))
        h = min(round(head / item_sec), n_items - 1)
        t = min(round(tail / item_sec), max(0, n_items - 1 - h))
        new_dur = max(item_sec, (n_items - h - t) * item_sec)
        self.conn.execute(
            "UPDATE patterns SET head_items = ?, tail_items = ?, duration = ?, "
            "updated_at = ? WHERE id = ?",
            (h, t, new_dur, _now(), pattern_id),
        )
        n = 0
        for c in self.clips(pattern_id):
            os_, oe = self._orig_bounds(c)
            ns = os_ + head
            ne = oe - tail
            if ne - ns < 0.2:                     # never collapse a clip to nothing
                ne = ns + 0.2
            self.conn.execute(
                "UPDATE clips SET start = ?, end = ?, preview = NULL WHERE id = ?",
                (ns, ne, c["id"]),
            )
            n += 1
        self.conn.commit()
        return n

    def propagate_from_clip(self, clip_id: int) -> tuple[int, float, float] | None:
        """Take one hand-refined clip as the reference and apply its correction
        to **every** clip of the same pattern: the head/tail it trimmed off its
        detected bounds become the pattern's refinement. Returns
        (clips_adjusted, head_seconds, tail_seconds), or None if unknown.

        This is the "I fixed one, fix the other 100 the same way" operation.
        """
        ref = self.clip(clip_id)
        if ref is None:
            return None
        os_, oe = self._orig_bounds(ref)
        head = max(0.0, ref["start"] - os_)
        tail = max(0.0, oe - ref["end"])
        n = self.trim_pattern(ref["pattern_id"], head, tail)
        return (n or 0, head, tail)

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
                f"- **{p['label']}** · {p['folder']} · "
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
                    f"  - {seg.get('label','?')} · "
                    f"{_fmt(seg.get('start',0))} → {_fmt(seg.get('end',0))}"
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

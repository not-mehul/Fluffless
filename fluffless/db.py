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
    items       TEXT NOT NULL,          -- JSON array of fingerprint integers
    duration    REAL NOT NULL,
    shows       INTEGER NOT NULL DEFAULT 1,
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
        conn.commit()
        return cls(path=path, conn=conn)

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
        return json.loads(row["items"])

    # --- clips ---------------------------------------------------------------

    def add_clip(
        self, pattern_id: int, file_path: str, start: float, end: float,
        preview: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO clips (pattern_id, file_path, file_name, start, end, "
            "preview, created_at) VALUES (?,?,?,?,?,?,?)",
            (pattern_id, file_path, os.path.basename(file_path), start, end, preview, _now()),
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

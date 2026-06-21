#!/usr/bin/env python3
"""Adhoc: turn a real media folder into a shareable "patterns" export.

Run the *exact same* scan pipeline the app uses (fingerprint → match known →
cross-file recurrence → dedupe → normalise), then dump the result to a single
JSON file you can hand to someone else to debug detection on real data.

What it captures, and what it does NOT
--------------------------------------
It captures the integer *fingerprint* streams (Chromaprint for audio, perceptual
hashes for video), the detected pattern "cards", and every clip's timestamps —
i.e. everything the detector reasons about. It does NOT capture any audio or
video: a fingerprint is a one-way fixed-rate hash stream, so the original media
cannot be reconstructed from it. That makes the export safe to share while still
letting the reader reproduce locate()/trim/fingerprint-choice behaviour exactly.

Usage
-----
    python export_patterns.py /path/to/media
    python export_patterns.py /path/to/media --folder "My Show" --min-len 25
    python export_patterns.py /path/to/media --anonymize          # mask names
    python export_patterns.py /path/to/media --no-fingerprints    # smaller file
    python export_patterns.py /path/to/media -o my_show.json

The JSON, plus a short Markdown summary next to it, is what you share.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile

from fluffless.binaries import detect_tools
from fluffless.db import Database
from fluffless.media import scan_library
from fluffless.repetition import DetectParams
from fluffless.scan import scan_folder


def _eta_print(payload: dict) -> None:
    """Compact one-line progress so a long scan isn't silent."""
    stage = payload.get("stage")
    if stage == "fingerprint":
        i, n = payload.get("index", 0) + 1, payload.get("total", 0)
        sys.stdout.write(f"\r  fingerprint {i}/{n}  {payload.get('file','')[:48]:<48}")
        sys.stdout.flush()
    elif stage == "detect":
        sys.stdout.write(f"\r  detecting across {payload.get('count')} files…{' ':<40}")
        sys.stdout.flush()
    elif stage == "detect_progress":
        d, t = payload.get("done", 0), payload.get("total", 1)
        pct = int(100 * d / t) if t else 0
        sys.stdout.write(f"\r  detecting… {pct}% ({d}/{t} pairs){' ':<20}")
        sys.stdout.flush()
    elif stage == "normalize":
        sys.stdout.write(f"\r  normalising {payload.get('count')} patterns…{' ':<30}")
        sys.stdout.flush()
    elif stage == "error":
        sys.stdout.write(f"\n  ! {payload.get('file','')}: {payload.get('message','')}\n")
    elif stage == "done":
        sys.stdout.write("\r" + " " * 78 + "\r")


def export_folder(db: Database, folder, *, include_fp: bool, name_of) -> dict:
    """Build the shareable dict for one scanned folder."""
    fps = db.fingerprints(folder.name)                 # [(path, Fingerprint)]
    files = []
    for path, fp in fps:
        entry = {
            "name": name_of(path),
            "duration": round(len(fp) * fp.item_sec, 2),
            "item_sec": round(fp.item_sec, 6),
            "bits": fp.bits,
            "n_items": len(fp),
        }
        if include_fp:
            entry["fingerprint"] = fp.items           # the integer stream itself
        files.append(entry)

    patterns = []
    for prow in db.patterns(folder.name):
        clips = [
            {
                "file": name_of(c["file_path"]),
                "start": round(c["start"], 3),
                "end": round(c["end"], 3),
                "dur": round(c["end"] - c["start"], 3),
            }
            for c in db.clips(prow["id"])
        ]
        pat = {
            "id": prow["id"],
            "status": prow["status"],
            "duration": round(prow["duration"], 3),
            "shows": prow["shows"],
            "n_clips": len(clips),
            "clips": clips,
        }
        if include_fp:
            pat["fingerprint"] = db.pattern_items(prow)
        patterns.append(pat)

    return {
        "name": folder.name,
        "kind": folder.kind,
        "files": files,
        "patterns": patterns,
    }


def _name_mapper(anonymize: bool):
    """Return a path→display-name function. With --anonymize the basenames are
    replaced by stable file001.ext labels so real titles never leave the box."""
    if not anonymize:
        return os.path.basename
    seen: dict[str, str] = {}

    def m(path: str) -> str:
        base = os.path.basename(path)
        if base not in seen:
            ext = os.path.splitext(base)[1]
            seen[base] = f"file{len(seen) + 1:03d}{ext}"
        return seen[base]

    return m


def write_markdown(out_json: str, export: dict) -> str:
    """A glanceable companion so you can eyeball what was detected."""
    md_path = os.path.splitext(out_json)[0] + ".md"
    lines = ["# Fluffless detection export", ""]
    lines.append(f"- created: {export['created']}")
    lines.append(f"- min length: {export['params']['min_seconds']}s, "
                 f"min shows: {export['params']['min_shows']}")
    lines.append("")
    for fol in export["folders"]:
        lines.append(f"## {fol['name']}  ({fol['kind']}, {len(fol['files'])} files)")
        lines.append("")
        if not fol["patterns"]:
            lines.append("_No recurring segments found._\n")
            continue
        for p in fol["patterns"]:
            lines.append(f"### Pattern {p['id']} — {p['duration']}s — "
                         f"{p['status']} — {p['n_clips']} occurrence(s)")
            for c in p["clips"]:
                lines.append(f"- `{c['file']}`  {c['start']}s → {c['end']}s  "
                             f"({c['dur']}s)")
            lines.append("")
    with open(md_path, "w") as fh:
        fh.write("\n".join(lines))
    return md_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("library", help="folder of media (a show folder, or a root of show folders)")
    ap.add_argument("--folder", help="only export this one sub-folder by name")
    ap.add_argument("--min-len", type=float, default=25.0,
                    help="minimum segment length in seconds (default 25, matches the app)")
    ap.add_argument("--min-shows", type=int, default=2,
                    help="files a segment must appear in (default 2)")
    ap.add_argument("--workers", type=int, default=0,
                    help="parallel workers (default: CPU count; 1 disables)")
    ap.add_argument("--no-fingerprints", action="store_true",
                    help="omit the raw fingerprint streams (much smaller file)")
    ap.add_argument("--anonymize", action="store_true",
                    help="replace file names with fileNNN labels")
    ap.add_argument("-o", "--out", default="fluffless_export.json",
                    help="output JSON path (default fluffless_export.json)")
    args = ap.parse_args()

    tools = detect_tools()
    status = tools.status()
    if not (status["fpcalc"] or status["ffmpeg"]):
        sys.exit("Need fpcalc (audio) and/or ffmpeg (video) on PATH to fingerprint.")
    print(f"engines: fpcalc={'ok' if status['fpcalc'] else 'missing'}, "
          f"ffmpeg={'ok' if status['ffmpeg'] else 'missing'}")

    folders = scan_library(args.library, tools)
    if args.folder:
        folders = [f for f in folders if f.name == args.folder]
    if not folders:
        sys.exit("No media folders found (or --folder name didn't match).")

    workers = args.workers if args.workers > 0 else (os.cpu_count() or 1)
    params = DetectParams(min_seconds=args.min_len, min_shows=args.min_shows)
    name_of = _name_mapper(args.anonymize)

    # Scan into a throwaway workspace so the user's media folder is never touched.
    with tempfile.TemporaryDirectory() as ws:
        db = Database.open(ws)
        out_folders = []
        for folder in folders:
            print(f"\n{folder.name}: {len(folder.files)} file(s)")
            scan_folder(
                db, args.library, folder.name, folder.files, tools,
                params=params, progress=_eta_print, make_preview=None,
                workers=workers,
            )
            fol = export_folder(db, folder, include_fp=not args.no_fingerprints,
                                name_of=name_of)
            n_pat = len(fol["patterns"])
            n_clip = sum(p["n_clips"] for p in fol["patterns"])
            print(f"  → {n_pat} pattern(s), {n_clip} occurrence(s)")
            out_folders.append(fol)
        db.close()

    export = {
        "fluffless_export": 1,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "anonymized": args.anonymize,
        "includes_fingerprints": not args.no_fingerprints,
        "params": {
            "min_seconds": params.min_seconds,
            "min_shows": params.min_shows,
            "max_bit_err": params.max_bit_err,
            "locate_min_ratio": params.locate_min_ratio,
            "dedupe_ratio": params.dedupe_ratio,
        },
        "folders": out_folders,
    }

    with open(args.out, "w") as fh:
        json.dump(export, fh)
    md = write_markdown(args.out, export)
    size_mb = os.path.getsize(args.out) / 1e6
    print(f"\nwrote {args.out} ({size_mb:.1f} MB) and {md}")
    if not args.no_fingerprints and size_mb > 25:
        print("  (large — re-run with --no-fingerprints if you only need the "
              "detected timestamps)")


if __name__ == "__main__":
    main()

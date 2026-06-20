"""Locate and run the external engines Fluffless leans on.

Name the engine, not the magic: *fpcalc* (Chromaprint) fingerprints audio,
*ffmpeg* extracts frames / clips / trims, *ffprobe* reads media metadata.
Nothing here is AI — these are the real tools doing the work.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


class BinaryMissing(RuntimeError):
    """Raised when a required external tool isn't installed."""


def which(name: str) -> str | None:
    return shutil.which(name)


@dataclass(frozen=True)
class Tools:
    ffmpeg: str | None
    ffprobe: str | None
    fpcalc: str | None

    @property
    def has_ffmpeg(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)

    @property
    def has_fpcalc(self) -> bool:
        return bool(self.fpcalc)

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self, n)]
        if missing:
            raise BinaryMissing(
                "Missing required tool(s): "
                + ", ".join(missing)
                + ". Install ffmpeg (provides ffmpeg + ffprobe) and "
                "chromaprint/fpcalc."
            )

    def status(self) -> dict[str, bool]:
        return {
            "ffmpeg": self.has_ffmpeg,
            "fpcalc": self.has_fpcalc,
        }


def detect_tools() -> Tools:
    return Tools(ffmpeg=which("ffmpeg"), ffprobe=which("ffprobe"), fpcalc=which("fpcalc"))


def run(cmd: list[str], *, timeout: int = 600) -> subprocess.CompletedProcess:
    """Run a command, capturing output. Raises RuntimeError with the tool's own
    stderr on failure, so the real reason (missing encoder, bad codec, …) is
    visible instead of an opaque exit code."""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        tool = os.path.basename(cmd[0]) if cmd else "command"
        detail = (proc.stderr or proc.stdout or "").strip()
        tail = detail.splitlines()[-4:] if detail else []
        raise RuntimeError(
            f"{tool} failed (exit {proc.returncode})"
            + (": " + " | ".join(tail) if tail else "")
        )
    return proc

#!/usr/bin/env python3
"""Minimal stdlib test runner — runs every ``test_*`` function in tests/.

Lets the suite run without pytest installed: ``python3 run_tests.py``.
"""
import importlib.util
import pathlib
import sys
import traceback

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    passed = failed = 0
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name in dir(mod):
            if not name.startswith("test_"):
                continue
            fn = getattr(mod, name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
                print(f"  ok   {path.name}::{name}")
            except Exception:  # noqa: BLE001
                failed += 1
                print(f"  FAIL {path.name}::{name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

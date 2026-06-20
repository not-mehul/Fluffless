"""Command-line entry point: ``python -m fluffless [LIBRARY] [--port N]``."""

from __future__ import annotations

import argparse
import os

from .server import serve

DEFAULT_LIBRARY = os.environ.get(
    "FLUFFLESS_LIBRARY",
    os.path.join(os.path.expanduser("~"), "Fluffless Media"),
)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fluffless",
        description="Find and remove repeated segments (ads, intros, outros) "
                    "from a folder of media.",
    )
    parser.add_argument(
        "library", nargs="?", default=None,
        help=f"library folder to open on start (default: {DEFAULT_LIBRARY} "
             "if it exists)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=7654, help="bind port (default 7654)")
    args = parser.parse_args()

    library = args.library
    if library is None and os.path.isdir(DEFAULT_LIBRARY):
        library = DEFAULT_LIBRARY

    serve(library, host=args.host, port=args.port)


if __name__ == "__main__":
    main()

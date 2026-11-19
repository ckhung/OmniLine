"""OmniLine frontend dispatch and session lifecycle."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from okline import OkLine
from okline._util import reconfigure_stdout_utf8

from .cli import run_history, run_list
from .tui import run_tui


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omniline")
    frontends = parser.add_subparsers(dest="frontend", required=True)
    tui = frontends.add_parser("tui", help="read-only terminal UI")
    tui.add_argument("--tokens-file", default=None, help="session JSON file")
    cli = frontends.add_parser("cli", help="command-line frontend")
    commands = cli.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="list friends and groups")
    listing.add_argument("-s", "--sort", choices=("lmtime", "name"), default="name")
    listing.add_argument("--json", action="store_true", help="full JSON output")
    listing.add_argument("--csv", action="store_true", help="short CSV output")
    listing.add_argument("-f", "--full", action="store_true", help="full JSON output")
    listing.add_argument("--tokens-file", default=None, help="session JSON file")

    history = commands.add_parser("hist", help="dump recent history for one chat")
    history.add_argument("-n", type=int, default=50, help="maximum number of messages")
    history.add_argument("needle", help="substring of a chat MID or display name")
    history.add_argument("--tokens-file", default=None, help="session JSON file")
    return parser


def _token_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    for candidate in (Path("tokens.json"), Path("worksp/tokens.json"), Path.home() / "tokens.json"):
        if candidate.exists():
            return candidate
    return Path("tokens.json")


def _main(argv: Sequence[str] | None = None) -> int:
    reconfigure_stdout_utf8()
    args = _build_parser().parse_args(argv)
    assert args.frontend in {"cli", "tui"}, "unsupported skeletal frontend"

    api = OkLine.from_tokens_file(str(_token_path(args.tokens_file)))
    try:
        assert api.tokens.access_token, "no access token in the session file"
        if args.frontend == "tui":
            return run_tui(api)
        if args.command == "list":
            return run_list(api, args)
        if args.command == "hist":
            return run_history(api, args)
        raise AssertionError("unsupported skeletal command")
    finally:
        api.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Run OmniLine, treating a closed downstream pipe as successful output."""
    try:
        return _main(argv)
    except BrokenPipeError:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""OmniLine's first command-line frontend.

The CLI stays intentionally thin.  Shared OkLine operations live in
``chat_service.py`` and shared user-facing models live in the flat sibling
modules.  Future ``tui`` and ``web`` frontends should call the same service
functions rather than invoking the CLI.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from okline import OkLine
from okline._util import reconfigure_stdout_utf8

from .chat_service import find_chats, list_chats, recent_messages
from .tui import run_tui
from .ufl_chat import uflChat


def _build_parser() -> argparse.ArgumentParser:
    # FUTURE: move frontend-specific parser construction to cli.py.
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
    listing.add_argument(
        "-f", "--full", action="store_true", help="full JSON output (alias for --json)"
    )
    listing.add_argument("--tokens-file", default=None, help="session JSON file")

    history = commands.add_parser("hist", help="dump recent history for one chat")
    history.add_argument("-n", type=int, default=50, help="maximum number of messages")
    history.add_argument("needle", help="substring of a chat MID or display name")
    history.add_argument("--tokens-file", default=None, help="session JSON file")
    # FUTURE options: --json, --csv, --reverse, --before, --after, --page-limit.
    return parser


def _token_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    for candidate in (Path("tokens.json"), Path("worksp/tokens.json"), Path.home() / "tokens.json"):
        if candidate.exists():
            return candidate
    return Path("tokens.json")


def _sort_chats(chats: list[uflChat], sort_name: str) -> list[uflChat]:
    if sort_name == "lmtime":
        # Empty timestamps sort last; newest conversations sort first.
        return sorted(chats, key=lambda chat: chat.last_message_time or "", reverse=True)
    return sorted(chats, key=lambda chat: (chat.name.casefold(), chat.id))


def _write_csv(chats: list[uflChat]) -> None:
    out = csv.writer(sys.stdout, lineterminator="\n")
    out.writerow(["id", "name", "kind", "last_message_time", "unread_count"])
    for chat in chats:
        out.writerow(
            [
                chat.id,
                chat.name,
                chat.kind,
                chat.last_message_time,
                "" if chat.unread_count is None else chat.unread_count,
            ]
        )


def _write_json(chats: list[uflChat]) -> None:
    json.dump([chat.to_dict(full=True) for chat in chats], sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def _write_matching_chats(chats: list[uflChat]) -> None:
    """Print ambiguous matches without fetching their histories."""
    out = csv.writer(sys.stdout, lineterminator="\n")
    for chat in chats:
        out.writerow([chat.id, chat.name])


def _write_history(messages: list[Any]) -> None:
    """Emit one normalized JSON message per line, like the existing probes."""
    for message in messages:
        json.dump(message.to_dict(), sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")


def _run_list(api: OkLine, args: argparse.Namespace) -> int:
    assert not ((args.json or args.full) and args.csv), "--json/--full and --csv conflict"
    chats = _sort_chats(list_chats(api), args.sort)
    if args.json or args.full:
        _write_json(chats)
    else:
        _write_csv(chats)
    return 0


def _run_history(api: OkLine, args: argparse.Namespace) -> int:
    assert args.n >= 1, "-n must be positive"
    matches = find_chats(list_chats(api), args.needle)
    if not matches:
        print(f'no chat matches "{args.needle}"', file=sys.stderr)
        return 1
    if len(matches) > 1:
        _write_matching_chats(matches)
        return 0
    _write_history(recent_messages(api, matches[0], args.n))
    return 0


def _main(argv: Sequence[str] | None = None) -> int:
    """Run the selected frontend command."""
    reconfigure_stdout_utf8()
    args = _build_parser().parse_args(argv)
    assert args.frontend in {"cli", "tui"}, "unsupported skeletal frontend"

    api = OkLine.from_tokens_file(str(_token_path(args.tokens_file)))
    try:
        assert api.tokens.access_token, "no access token in the session file"
        if args.frontend == "tui":
            return run_tui(api)
        if args.command == "list":
            return _run_list(api, args)
        if args.command == "hist":
            return _run_history(api, args)
        raise AssertionError("unsupported skeletal command")
    finally:
        api.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI, treating a closed downstream pipe as successful output."""
    try:
        return _main(argv)
    except BrokenPipeError:
        # ``head`` closes stdout after consuming its quota.  Redirect before
        # interpreter shutdown so Python does not report a second flush error.
        sys.stdout = open(  # noqa: SIM115 - stdout must remain redirected
            os.devnull, "w", encoding="utf-8"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

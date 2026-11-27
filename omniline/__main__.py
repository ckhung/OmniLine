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
from .config import AppConfig, init_user_directory, load_config
from .tui import run_tui
from .web import run_web


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omniline")
    parser.add_argument("--config", default=None, help="configuration TOML file")
    frontends = parser.add_subparsers(dest="frontend", required=True)
    init = frontends.add_parser("init", help="initialize ~/.omniline")
    init.add_argument("--force", action="store_true", help="overwrite existing files")
    web = frontends.add_parser("web", help="authenticated web frontend")
    web.add_argument("--host", default="127.0.0.1", help="development-server bind address")
    web.add_argument("--port", type=int, default=1730, help="development-server port")
    web.add_argument("--init-auth", action="store_true", help="initialize web password files")
    web.add_argument(
        "--password-file",
        default=None,
        help="temporary password.hash file for this web process",
    )
    tui = frontends.add_parser("tui", help="read-only terminal UI")
    tui.add_argument("--tokens-file", default=None, help="session JSON file")
    tui.add_argument("--theme-file", default=None, help="temporary TUI theme TOML file")
    cli = frontends.add_parser("cli", help="command-line frontend")
    commands = cli.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="list friends and groups")
    listing.add_argument("-s", "--sort", choices=("lmtime", "name"), default=None)
    listing.add_argument("--json", action="store_true", help="full JSON output")
    listing.add_argument("--csv", action="store_true", help="short CSV output")
    listing.add_argument("-f", "--full", action="store_true", help="full JSON output")
    listing.add_argument("--tokens-file", default=None, help="session JSON file")

    history = commands.add_parser("hist", help="dump recent history for one chat")
    history.add_argument("-n", type=int, default=None, help="maximum number of messages")
    history.add_argument("needle", help="substring of a chat MID or display name")
    history.add_argument("--tokens-file", default=None, help="session JSON file")
    return parser


def _token_path(explicit: str | None, config: AppConfig) -> Path:
    if explicit:
        return Path(explicit)
    return config.tokens_file


def _main(argv: Sequence[str] | None = None) -> int:
    reconfigure_stdout_utf8()
    args = _build_parser().parse_args(argv)
    if args.frontend == "init":
        try:
            written, skipped = init_user_directory(force=args.force)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"omniline: cannot initialize user directory: {exc}", file=sys.stderr)
            return 2
        for path in written:
            print(f"created {path}")
        for path in skipped:
            print(f"kept existing {path}")
        return 0
    if args.frontend == "web":
        try:
            return run_web(
                args.host,
                args.port,
                init_auth=args.init_auth,
                password_file=args.password_file,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"omniline: cannot run web frontend: {exc}", file=sys.stderr)
            return 2
    try:
        config = load_config(args.config, args.theme_file if args.frontend == "tui" else None)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"omniline: cannot load configuration: {exc}", file=sys.stderr)
        return 2
    if args.tokens_file is None:
        args.tokens_file = str(config.tokens_file)
    if args.frontend == "cli" and args.command == "list" and args.sort is None:
        args.sort = config.cli_sort
    if args.frontend == "cli" and args.command == "hist" and args.n is None:
        args.n = config.history_limit

    try:
        api = OkLine.from_tokens_file(str(_token_path(args.tokens_file, config)))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"omniline: cannot load session: {exc}", file=sys.stderr)
        return 2
    try:
        if not api.tokens.access_token:
            raise ValueError("session file has no access token; log in again")
        if args.frontend == "tui":
            return run_tui(api, config)
        if args.command == "list":
            return run_list(api, args)
        if args.command == "hist":
            return run_history(api, args)
        raise ValueError("unsupported frontend command")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"omniline: {exc}", file=sys.stderr)
        return 2
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

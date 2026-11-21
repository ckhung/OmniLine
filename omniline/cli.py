"""Command-line rendering and command handlers shared by future frontends."""

from __future__ import annotations

import csv
import json
import sys
from argparse import Namespace
from typing import Any

from .chat_service import find_chats, list_chats, recent_messages
from .ufl import sort_chats, uflChat


def write_csv(chats: list[uflChat], stream: Any = sys.stdout) -> None:
    """Write the compact conversation listing."""
    out = csv.writer(stream, lineterminator="\n")
    out.writerow(["id", "name", "kind", "last_message_time", "unread_count"])
    for chat in chats:
        out.writerow([
            chat.id,
            chat.name,
            chat.kind,
            chat.last_message_time,
            "" if chat.unread_count is None else chat.unread_count,
        ])


def write_json(chats: list[uflChat], stream: Any = sys.stdout) -> None:
    json.dump([chat.to_dict(full=True) for chat in chats], stream, ensure_ascii=False)
    stream.write("\n")


def write_matching_chats(chats: list[uflChat], stream: Any = sys.stdout) -> None:
    """Write ambiguous matches without retrieving their histories."""
    out = csv.writer(stream, lineterminator="\n")
    for chat in chats:
        out.writerow([chat.id, chat.name])


def write_history(messages: list[Any], stream: Any = sys.stdout) -> None:
    for message in messages:
        output = message.to_dict()
        output.pop("raw", None)
        json.dump(output, stream, ensure_ascii=False)
        stream.write("\n")


def run_list(api: Any, args: Namespace, stream: Any = sys.stdout) -> int:
    if (args.json or args.full) and args.csv:
        raise ValueError("choose either --json/--full or --csv")
    chats = sort_chats(list_chats(api))
    if args.sort == "name":
        chats = sorted(chats, key=lambda chat: (chat.name.casefold(), chat.id))
    if args.json or args.full:
        write_json(chats, stream)
    else:
        write_csv(chats, stream)
    return 0


def run_history(api: Any, args: Namespace, stream: Any = sys.stdout, error: Any = sys.stderr) -> int:
    if args.n < 1:
        raise ValueError("-n must be positive")
    matches = find_chats(list_chats(api), args.needle)
    if not matches:
        print(f'no chat matches "{args.needle}"', file=error)
        return 1
    if len(matches) > 1:
        write_matching_chats(matches, stream)
        return 0
    write_history(recent_messages(api, matches[0], args.n), stream)
    return 0

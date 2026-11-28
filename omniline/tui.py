"""Read-only prompt_toolkit frontend for OmniLine."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from prompt_toolkit.application import Application, get_app, run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.screen import Point
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from .chat_service import (
    download_media,
    list_chats,
    mark_chat_read,
    media_reference,
    recent_messages,
    refresh_unread_counts,
    resolve_contact_names,
)
from .config import AppConfig, load_config
from .ufl import (
    TAIPEI,
    fit_display_name,
    format_unread_count,
    repeat_pattern,
    sort_chats,
    uflChat,
    uflGroup,
    uflMessage,
)

URL_PATTERN = re.compile(r"https?://[^\s<>()]+")


def _first_url(text: str) -> str | None:
    """Return the first URL in a rendered content line."""
    match = URL_PATTERN.search(text)
    return match.group(0).rstrip(".,;:!?]}") if match else None


def _open_url(browser: str, url: str) -> None:
    subprocess.run([browser, url], check=False)


def _open_media(viewer: str, data: bytes, name: str) -> None:
    """Open downloaded media in an external viewer, then remove the temp file."""
    suffix = Path(name).suffix or ".bin"
    with tempfile.NamedTemporaryFile(prefix="omniline-", suffix=suffix) as stream:
        stream.write(data)
        stream.flush()
        subprocess.run([viewer, stream.name], check=False)


def _wrap_display_cells(text: str, width: int) -> list[str]:
    """Wrap text without splitting a terminal display cell."""
    if width < 1:
        raise ValueError("display width must be positive")
    lines: list[str] = []
    for part in text.splitlines() or [""]:
        current = ""
        for char in part:
            if current and get_cwidth(current + char) > width:
                lines.append(current)
                current = ""
            current += char
        lines.append(current)
    return lines


@dataclass
class TuiState:
    """Mutable state kept separate from prompt_toolkit widgets."""

    api: Any
    chats: list[uflChat]
    config: AppConfig = field(default_factory=load_config)
    mode: str = "list"
    cursor: int = 0
    ordinary_offset: int = 0
    query: str = ""
    search_mode: str | None = None
    search_buffer: Buffer = field(default_factory=Buffer)
    compose_mode: str | None = None
    compose_confirm: bool = False
    compose_buffer: Buffer = field(default_factory=Buffer)
    selected_chat: uflChat | None = None
    messages: list[uflMessage] = field(default_factory=list)
    content_lines: list[list[tuple[str, str]]] = field(default_factory=list)
    content_offset: int = 0
    content_query: str = ""
    content_matches: list[tuple[int, int, int]] = field(default_factory=list)
    content_cursor: int | None = None
    content_cursor_line: int = 0
    reply_targets: dict[int, str] = field(default_factory=dict)
    media_targets: dict[int, uflMessage] = field(default_factory=dict)
    search_direction: int = -1
    sender_names: dict[str, str] = field(default_factory=dict)
    app: Application | None = None
    status_error: str | None = None

    @property
    def self_mid(self) -> str | None:
        return getattr(self.api.tokens, "mid", None)

    @property
    def pinned(self) -> list[uflChat]:
        return [chat for chat in self.chats if chat.pinned]

    @property
    def rows(self) -> list[uflChat]:
        return sort_chats(self.chats, self.query)

    @property
    def ordinary(self) -> list[uflChat]:
        return [chat for chat in self.rows if not chat.pinned]

    @property
    def displayed_count(self) -> int:
        return len(self.rows)

    @property
    def current_chat(self) -> uflChat | None:
        rows = self.rows
        return rows[self.cursor] if rows and self.cursor < len(rows) else None

    @property
    def name_by_mid(self) -> dict[str, str]:
        names = {chat.id: chat.name for chat in self.chats if chat.kind == "contact"}
        names.update(self.sender_names)
        return names

    def invalidate(self) -> None:
        if self.app:
            self.app.invalidate()

    def visible_ordinary_rows(self) -> int:
        rows = get_app().output.get_size().rows
        return max(1, rows - len(self.pinned) - 3 - (self.search_mode == "list"))

    def ensure_cursor(self) -> None:
        count = self.displayed_count
        self.cursor = min(max(self.cursor, 0), max(0, count - 1))
        favorite_count = len(self.pinned)
        if self.cursor < favorite_count:
            return
        ordinary_index = self.cursor - favorite_count
        visible = self.visible_ordinary_rows()
        if ordinary_index < self.ordinary_offset:
            self.ordinary_offset = ordinary_index
        if ordinary_index >= self.ordinary_offset + visible:
            self.ordinary_offset = ordinary_index - visible + 1
        max_offset = max(0, len(self.ordinary) - visible)
        self.ordinary_offset = min(self.ordinary_offset, max_offset)

    def move_cursor(self, delta: int) -> None:
        self.cursor += delta
        self.ensure_cursor()
        self.invalidate()

    def begin_search(self) -> None:
        self.search_mode = self.mode
        self.search_buffer.reset()
        self.search_buffer.text = self.query if self.mode == "list" else self.content_query
        self.invalidate()

    def submit_search(self) -> None:
        text = self.search_buffer.text
        mode = self.search_mode
        self.search_mode = None
        if mode == "list":
            self.query = text
            self.cursor = 0
            self.ordinary_offset = 0
            self.ensure_cursor()
        elif mode == "content":
            self.content_query = text
            if text:
                self.find_content_matches()
            else:
                self.content_matches = []
                self.content_cursor = None
        self.search_buffer.reset()
        self.invalidate()

    def cancel_search(self) -> None:
        if self.search_mode == "list":
            self.query = ""
            self.cursor = 0
            self.ordinary_offset = 0
        self.search_mode = None
        self.search_buffer.reset()
        self.invalidate()

    def restore_full_list(self) -> None:
        """Remove a completed list filter; leave an already-full list unchanged."""
        if self.mode == "list" and self.query:
            self.query = ""
            self.cursor = 0
            self.ordinary_offset = 0
            self.ensure_cursor()
            self.invalidate()

    def refresh_counts(self) -> None:
        if self.mode != "list":
            return
        refresh_unread_counts(self.api, self.chats)
        self.ensure_cursor()
        self.invalidate()

    def enter_profile(self) -> None:
        chat = self.current_chat
        if chat is None:
            return
        self.selected_chat = chat
        self.mode = "profile"
        self.invalidate()

    def toggle_pin(self) -> None:
        if self.mode != "profile":
            return
        chat = self.current_chat
        if chat is None:
            return
        old_id = chat.id
        pinned = [row.id for row in self.chats if row.pinned and row.id != old_id]
        if not chat.pinned:
            pinned.append(old_id)
        self.config.save_pinned_chats(pinned)
        chat.pinned = not chat.pinned
        rows = self.pinned + self.ordinary
        self.cursor = next(index for index, row in enumerate(rows) if row.id == old_id)
        self.ensure_cursor()
        self.invalidate()

    def enter_content(self) -> None:
        chat = self.current_chat
        if chat is None:
            return
        unread_count = chat.unread_count or 0
        self.selected_chat = chat
        self.messages = list(reversed(recent_messages(self.api, chat, self.config.history_limit)))
        sender_mids = {
            message.from_mid
            for message in self.messages
            if message.from_mid and message.from_mid != self.self_mid
        }
        self.sender_names.update(resolve_contact_names(self.api, sorted(sender_mids)))
        self.content_lines = self.make_message_lines(chat, self.messages, unread_count)
        mark_chat_read(self.api, chat, self.messages)
        self.content_offset = max(0, len(self.content_lines) - self.content_height())
        self.content_cursor_line = max(0, len(self.content_lines) - 1)
        self.content_query = ""
        self.content_matches = []
        self.content_cursor = None
        self.mode = "content"
        self.invalidate()

    def compose_message(self) -> None:
        """Open the in-application compose dialog at the content cursor."""
        if self.mode != "content":
            return
        chat = self.selected_chat
        if chat is None:
            return
        reply_id = self.reply_targets.get(self.content_cursor_line)
        is_new_message = self.content_cursor_line == len(self.content_lines) - 1
        if (
            not reply_id
            and not is_new_message
            and self.config.tui_send_only_on_fixed_lines
        ):
            return
        self.compose_mode = "reply" if reply_id else "new"
        self.compose_confirm = False
        self.compose_buffer.reset()
        self.invalidate()

    def submit_compose(self) -> None:
        """Advance compose input to confirmation, or cancel blank input."""
        if not self.compose_buffer.text.strip():
            self.cancel_compose()
            return
        if self.config.tui_send_confirmation:
            self.compose_confirm = True
            self.invalidate()
        else:
            self.confirm_compose()

    def confirm_compose(self) -> None:
        if self.compose_mode not in {"new", "reply"}:
            return
        chat = self.selected_chat
        if chat is None:
            return
        reply_id = self.reply_targets.get(self.content_cursor_line)
        text = self.compose_buffer.text
        if reply_id:
            self.api.reply_text(chat.id, text, related_message_id=reply_id)
        else:
            self.api.send_text(chat.id, text)
        self.cancel_compose()
        self.enter_content()

    def cancel_compose(self) -> None:
        self.compose_mode = None
        self.compose_confirm = False
        self.compose_buffer.reset()
        self.invalidate()

    def leave_content(self) -> None:
        self.mode = "list"
        self.selected_chat = None
        self.invalidate()

    def content_height(self) -> int:
        rows = get_app().output.get_size().rows
        return max(1, rows - 2 - (self.search_mode == "content"))

    def move_content(self, delta: int) -> None:
        if not self.content_lines:
            return
        self.content_cursor_line = min(
            max(0, self.content_cursor_line + delta), len(self.content_lines) - 1
        )
        self.reveal_line_cursor(1 if delta > 0 else -1)
        self.invalidate()

    def find_content_matches(self) -> None:
        if not self.content_query:
            self.content_matches = []
            self.content_cursor = None
            return
        self.content_matches = []
        for line_index, line in enumerate(self.content_lines):
            text = "".join(part for _, part in line)
            start = 0
            while (match_start := text.find(self.content_query, start)) >= 0:
                self.content_matches.append(
                    (line_index, match_start, match_start + len(self.content_query))
                )
                start = match_start + 1
        if not self.content_matches:
            self.content_cursor = None
            return
        # Content is oldest-first, so the last match is in the most recent
        # matching message.
        self.content_cursor = len(self.content_matches) - 1
        self.reveal_content_cursor(0)

    def next_content_match(self) -> None:
        self.move_content_match(1)

    def previous_content_match(self) -> None:
        self.move_content_match(-1)

    def move_content_match(self, step: int) -> None:
        if not self.content_matches:
            return
        current = self.content_cursor if self.content_cursor is not None else 0
        self.content_cursor = (current + step) % len(self.content_matches)
        self.reveal_content_cursor(step)

    def reveal_content_cursor(self, direction: int) -> None:
        if self.content_cursor is None:
            return
        self.content_cursor_line = self.content_matches[self.content_cursor][0]
        self.reveal_line_cursor(direction)

    def reveal_line_cursor(self, direction: int) -> None:
        height = self.content_height()
        maximum = max(0, len(self.content_lines) - height)
        if self.content_offset <= self.content_cursor_line < self.content_offset + height:
            return
        if direction > 0:
            # Forward search: keep the match at the top, delaying scrolling
            # until the bottom of the available content is reached.
            self.content_offset = min(self.content_cursor_line, maximum)
        elif direction < 0:
            # Backward search: keep the match at the bottom for the same reason.
            self.content_offset = min(
                max(0, self.content_cursor_line - height + 1), maximum
            )
        else:
            # The initial match is the newest one; show it at the bottom.
            self.content_offset = min(
                max(0, self.content_cursor_line - height + 1), maximum
            )

    def content_cursor_position(self) -> Point | None:
        if self.mode != "content":
            return None
        y = self.content_cursor_line - self.content_offset
        return Point(x=0, y=y) if 0 <= y < self.content_height() else None

    def current_content_url(self) -> str | None:
        if self.mode != "content" or not self.content_lines:
            return None
        line = self.content_lines[self.content_cursor_line]
        return _first_url("".join(text for _, text in line))

    def current_content_media(self) -> uflMessage | None:
        """Return media at the current history line, if any."""
        message = self.media_targets.get(self.content_cursor_line)
        return message if message and media_reference(message) else None

    def make_message_lines(
        self, chat: uflChat, messages: list[uflMessage], unread_count: int = 0
    ) -> list[list[tuple[str, str]]]:
        width = max(16, get_app().output.get_size().columns * 2 // 3)
        text_width = max(1, width - 4)
        names = self.name_by_mid
        lines: list[list[tuple[str, str]]] = []
        self.reply_targets = {}
        self.media_targets = {}
        previous_date = None
        unread_boundary = len(messages) - max(0, unread_count)
        unread_boundary = min(max(0, unread_boundary), len(messages))
        lines.append([("class:boundary", "#" * width)])
        for message_index, message in enumerate(messages):
            own = message.from_mid == self.self_mid
            if message_index == unread_boundary:
                lines.append([("class:boundary", "." * width)])
            message_date = (
                datetime.fromtimestamp(message.created_time / 1000, TAIPEI).date()
                if message.created_time is not None
                else None
            )
            if message_date is not None:
                if message_date != previous_date:
                    lines.append([("class:date", "📅 " + message_date.strftime("%m/%d"))])
                previous_date = message_date
            if not own:
                sender = (
                    chat.name
                    if chat.kind == "contact"
                    else names.get(message.from_mid or "", message.from_mid or "?")
                )
                timestamp = (
                    datetime.fromtimestamp(message.created_time / 1000, TAIPEI).strftime("%H:%M ")
                    if message.created_time is not None
                    else "      "
                )
                self.reply_targets[len(lines)] = message.id
                lines.append([("", timestamp), ("class:sender", sender)])
            media = media_reference(message)
            if media:
                self.media_targets[len(lines)] = message
            text = (
                f"[{media['kind']}: {media['name']}]"
                if media
                else message.text or f"[{message.availability}]"
            )
            if own:
                wrapped = _wrap_display_cells(text, text_width)
                box_width = max(1, max(get_cwidth(line) for line in wrapped))
                for line in wrapped:
                    box = line + " " * (box_width - get_cwidth(line))
                    lines.append(
                        [("", " " * (width - box_width)), ("class:own-box", box)]
                    )
            else:
                for part in text.splitlines() or [""]:
                    wrapped = textwrap.wrap(part, width=text_width) or [""]
                    for line in wrapped:
                        lines.append([("class:other", line.ljust(text_width) + " " * 4)])
        lines.append([("class:anchor", repeat_pattern("* ", width))])
        return lines

    def list_fragments(self) -> list[tuple[str, str]]:
        favorites = self.pinned
        ordinary = self.ordinary
        visible = ordinary[self.ordinary_offset : self.ordinary_offset + self.visible_ordinary_rows()]
        rows = favorites + visible
        first_ordinary = len(favorites) + self.ordinary_offset
        width = max(12, get_app().output.get_size().columns // 3)
        name_width = max(1, width - 4)
        fragments: list[tuple[str, str]] = []
        for local_index, chat in enumerate(rows):
            global_index = local_index if local_index < len(favorites) else first_ordinary + local_index - len(favorites)
            name, name_padding = fit_display_name(chat.name or chat.id, name_width)
            count_field = format_unread_count(chat.unread_count)
            selected = global_index == self.cursor
            if selected and self.mode == "list":
                style = "class:selected"
                fragments.append((style, count_field + name + name_padding + "\n"))
            elif selected:
                count_style = "class:favorite" if chat.pinned else ""
                name_style = "class:favorite-underline" if chat.pinned else "class:underline"
                fragments.append((count_style, count_field))
                if name_padding:
                    fragments.append((name_style, name))
                    fragments.append((count_style, name_padding + "\n"))
                else:
                    fragments.append((name_style, name + "\n"))
            else:
                style = "class:favorite" if chat.pinned else ""
                fragments.append((style, count_field + name + name_padding + "\n"))
        return fragments or [("class:dim", "(no chats)\n")]


    def profile_fragments(self) -> list[tuple[str, str]]:
        chat = self.selected_chat or self.current_chat
        if chat is None:
            return [("class:dim", "No chat selected.\n")]
        raw = getattr(chat, "raw", {})
        if not isinstance(raw, dict):
            raw = {}
        lines = [
            ("class:title", chat.name or chat.id),
            ("", f"kind: {chat.kind}\n"),
            ("", f"mid: {chat.id}\n"),
        ]
        if isinstance(chat, uflGroup):
            lines.append(("", f"members: {chat.member_count}\n"))
            description = raw.get("chatName", "")
        else:
            description = raw.get("statusMessage", "")
        if description:
            lines.append(("", f"{description}\n"))
        return lines

    def content_fragments(self) -> list[tuple[str, str]]:
        if self.mode == "profile":
            return self.profile_fragments()
        if self.mode != "content":
            return [("class:dim", "Select a chat with Enter.\n")]
        start = self.content_offset
        end = start + self.content_height()
        fragments: list[tuple[str, str]] = []
        for index, line in enumerate(self.content_lines[start:end], start):
            if index == self.content_cursor_line and index == len(self.content_lines) - 1:
                line = [("class:anchor-cursor", text) for _, text in line]
            elif index == self.content_cursor_line and all(
                not text.strip() for _, text in line
            ):
                line = [("class:cursor-empty", text) for _, text in line]
            line_matches = [
                (match_start, match_end, match_index)
                for match_index, (match_line, match_start, match_end) in enumerate(
                    self.content_matches
                )
                if match_line == index
            ]
            position = 0
            for style, text in line:
                end_position = position + len(text)
                boundaries = {position, end_position}
                for match_start, match_end, _ in line_matches:
                    if match_start < end_position and match_end > position:
                        boundaries.update(
                            bound for bound in (match_start, match_end)
                            if position <= bound <= end_position
                        )
                ordered = sorted(boundaries)
                for left, right in zip(ordered, ordered[1:]):
                    segment_style = style
                    for match_start, match_end, match_index in line_matches:
                        if left < match_end and right > match_start:
                            segment_style = (
                                "class:match"
                                if match_index == self.content_cursor
                                else "class:other-match"
                            )
                            break
                    fragments.append((segment_style, text[left - position : right - position]))
                position = end_position
            fragments.append(("", "\n"))
        return fragments or [("class:dim", "(no messages)\n")]

    def status_text(self) -> str:
        if self.compose_mode:
            return (
                "y: confirm send   n/Esc: cancel"
                if self.compose_confirm
                else "Enter: confirm   Esc: cancel"
            )
        if self.search_mode:
            return "Enter: submit   Esc: cancel   " + self.search_buffer.text
        if self.mode == "list":
            return "/ search   Enter/l: messages   p: profile   q: quit"
        if self.mode == "profile":
            return "P: pin/unpin   h/Left/Esc: back"
        if self.status_error:
            return self.status_error
        return "/ search   Enter: open URL   n: next, p: previous   R: send/reply   h/Left/Esc: chats   q: quit"


def _key_bindings(
    state: TuiState,
    search_window: Window,
    list_window: Window,
    content_window: Window,
    compose_window: Window,
) -> KeyBindings:
    """Bind keys by active panel; profile mode intentionally has no navigation."""
    keys = KeyBindings()
    list_active = Condition(
        lambda: state.search_mode is None and state.compose_mode is None and state.mode == "list"
    )
    content_active = Condition(
        lambda: state.search_mode is None
        and state.compose_mode is None
        and state.mode == "content"
    )
    panel_active = Condition(
        lambda: state.search_mode is None
        and state.compose_mode is None
        and state.mode in {"list", "content", "profile"}
    )
    searchable = Condition(
        lambda: state.search_mode is None
        and state.compose_mode is None
        and state.mode in {"list", "content"}
    )

    def focus_list(event: Any) -> None:
        event.app.layout.focus(list_window)

    def focus_content(event: Any) -> None:
        event.app.layout.focus(content_window)

    def focus_compose(event: Any) -> None:
        event.app.layout.focus(compose_window)

    @keys.add("up", filter=list_active)
    @keys.add("k", filter=list_active)
    def _list_up(event: Any) -> None:
        state.move_cursor(-1)

    @keys.add("down", filter=list_active)
    @keys.add("j", filter=list_active)
    def _list_down(event: Any) -> None:
        state.move_cursor(1)

    @keys.add("up", filter=content_active)
    @keys.add("k", filter=content_active)
    def _content_up(event: Any) -> None:
        state.move_content(-1)

    @keys.add("down", filter=content_active)
    @keys.add("j", filter=content_active)
    def _content_down(event: Any) -> None:
        state.move_content(1)

    @keys.add("pageup", filter=list_active)
    def _list_page_up(event: Any) -> None:
        state.move_cursor(-state.visible_ordinary_rows())

    @keys.add("pagedown", filter=list_active)
    def _list_page_down(event: Any) -> None:
        state.move_cursor(state.visible_ordinary_rows())

    @keys.add("pageup", filter=content_active)
    def _content_page_up(event: Any) -> None:
        state.move_content(-state.content_height())

    @keys.add("pagedown", filter=content_active)
    def _content_page_down(event: Any) -> None:
        state.move_content(state.content_height())

    @keys.add("/", filter=searchable)
    def _search(event: Any) -> None:
        state.begin_search()
        event.app.layout.focus(search_window)

    @keys.add("enter")
    async def _enter(event: Any) -> None:
        if state.compose_mode:
            if state.compose_confirm:
                state.confirm_compose()
            else:
                state.submit_compose()
        elif state.search_mode:
            state.submit_search()
            focus_list(event) if state.mode == "list" else focus_content(event)
        elif state.mode == "list" and state.current_chat is not None:
            state.enter_content()
            focus_content(event)
        elif state.mode == "content":
            media = state.current_content_media()
            if media:
                viewer = shutil.which("feh") or shutil.which("geeqie")
                if viewer is None:
                    state.status_error = "Image viewer unavailable: install feh or geeqie"
                    state.invalidate()
                    return
                try:
                    data, reference = download_media(state.api, media)
                except (OSError, RuntimeError, ValueError) as exc:
                    state.status_error = f"Media unavailable: {exc}"
                    state.invalidate()
                    return
                state.status_error = None
                await run_in_terminal(
                    lambda: _open_media(viewer, data, reference["name"])
                )
                event.app.invalidate()
            else:
                url = state.current_content_url()
                if not url:
                    return
                browser = shutil.which("w3m") or shutil.which("lynx")
                if browser is None:
                    state.status_error = "URL browser unavailable: install w3m or lynx"
                    state.invalidate()
                    return
                state.status_error = None
                await run_in_terminal(lambda: _open_url(browser, url))
                event.app.invalidate()

    @keys.add("escape")
    def _escape(event: Any) -> None:
        if state.compose_mode:
            state.cancel_compose()
            focus_content(event)
        elif state.search_mode:
            state.cancel_search()
        elif state.mode == "list":
            state.restore_full_list()
        else:
            state.leave_content()
            focus_list(event)

    @keys.add("left", filter=panel_active)
    @keys.add("h", filter=panel_active)
    def _back(event: Any) -> None:
        if state.mode == "list":
            state.restore_full_list()
        else:
            state.leave_content()
            focus_list(event)

    @keys.add("right", filter=list_active)
    @keys.add("l", filter=list_active)
    def _forward(event: Any) -> None:
        if state.current_chat is not None:
            state.enter_content()
            focus_content(event)

    @keys.add("p", filter=list_active)
    def _profile(event: Any) -> None:
        if state.current_chat is not None:
            state.enter_profile()
            focus_content(event)

    @keys.add("p", filter=content_active)
    def _previous_match(event: Any) -> None:
        if state.content_query:
            state.previous_content_match()
            state.invalidate()

    @keys.add(
        "P",
        filter=Condition(
            lambda: state.search_mode is None
            and state.compose_mode is None
            and state.mode == "profile"
        ),
    )
    def _toggle_pin(event: Any) -> None:
        try:
            state.toggle_pin()
        except (OSError, RuntimeError, ValueError) as exc:
            state.status_error = f"Cannot save pin: {exc}"
            state.invalidate()

    @keys.add("R", filter=list_active)
    def _refresh(event: Any) -> None:
        state.refresh_counts()

    @keys.add("n", filter=content_active)
    def _next_match(event: Any) -> None:
        if state.content_query:
            state.next_content_match()
            state.invalidate()

    @keys.add("R", filter=content_active)
    def _compose(event: Any) -> None:
        state.compose_message()
        if state.compose_mode:
            focus_compose(event)

    @keys.add("y", filter=Condition(lambda: state.compose_confirm))
    def _confirm_compose(event: Any) -> None:
        state.confirm_compose()
        focus_content(event)

    @keys.add("n", filter=Condition(lambda: state.compose_confirm))
    def _cancel_compose(event: Any) -> None:
        state.cancel_compose()
        focus_content(event)

    @keys.add(
        "q",
        filter=Condition(
            lambda: state.search_mode is None
            and state.compose_mode is None
            and state.mode in {"list", "content"}
        ),
    )
    def _quit_or_back(event: Any) -> None:
        if state.mode == "list":
            event.app.exit()
        else:
            state.leave_content()
            focus_list(event)

    @keys.add("c-c")
    def _ctrl_c(event: Any) -> None:
        event.app.exit()

    return keys


def run_tui(api: Any, config: AppConfig | None = None) -> int:
    """Run the read-only TUI against an already-authenticated API client."""
    config = config or load_config()
    chats = list_chats(api)
    for chat in chats:
        chat.pinned = chat.id in config.pinned_chats
    state = TuiState(api=api, chats=chats, config=config)
    list_window = Window(
        FormattedTextControl(cast(Any, state.list_fragments), focusable=True, show_cursor=False),
        width=Dimension(weight=1),
        wrap_lines=False,
        style="class:list-panel",
    )
    content_window = Window(
        FormattedTextControl(
            cast(Any, state.content_fragments),
            focusable=True,
            get_cursor_position=state.content_cursor_position,
        ),
        width=Dimension(weight=2),
        wrap_lines=False,
        style="class:content-panel",
        cursorline=True,
    )
    search_window = Window(
        BufferControl(state.search_buffer),
        height=1,
        style="class:search",
        wrap_lines=False,
    )
    compose_window = Window(
        BufferControl(state.compose_buffer),
        height=state.config.tui_compose_height,
        style="class:compose",
        wrap_lines=True,
    )
    status_window = Window(
        FormattedTextControl(lambda: [("class:status", state.status_text())]),
        height=1,
    )
    body = HSplit([
        VSplit([
            list_window,
            Window(char="|", width=Dimension.exact(1), style="class:divider"),
            content_window,
        ]),
        Window(FormattedTextControl(lambda: []), height=0),
        ConditionalContainer(search_window, Condition(lambda: state.search_mode is not None)),
        ConditionalContainer(compose_window, Condition(lambda: state.compose_mode is not None)),
        status_window,
    ])
    style = Style.from_dict(config.tui_theme)
    application: Application[None] = Application(
        layout=Layout(body, focused_element=list_window),
        key_bindings=_key_bindings(
            state, search_window, list_window, content_window, compose_window
        ),
        style=style,
        full_screen=True,
        mouse_support=False,
    )
    state.app = application
    application.run()
    return 0

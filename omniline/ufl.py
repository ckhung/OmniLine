"""User-facing OmniLine models and presentation-neutral helpers.

The CLI, TUI, and future web frontend should exchange these objects rather than
wire dictionaries or prompt_toolkit-specific fragments.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from wcwidth import wcswidth

from okline.entities import Contact, Group

TAIPEI = ZoneInfo("Asia/Taipei")


def _cell_width(text: str) -> int:
    return max(0, wcswidth(text))


def repeat_pattern(pattern: str, width: int) -> str:
    """Repeat a configured text pattern until it fills ``width`` cells."""
    assert pattern
    assert width >= 0
    result = ""
    while _cell_width(result + pattern) <= width:
        result += pattern
    for char in pattern:
        if _cell_width(result + char) > width:
            break
        result += char
    return result


def fit_display_name(name: str, width: int) -> tuple[str, str]:
    """Return ``(visible_name, trailing_padding)`` by terminal-cell width."""
    if _cell_width(name) <= width:
        return name, " " * (width - _cell_width(name))
    if width == 1:
        return "…", ""
    visible = ""
    for char in name:
        if _cell_width(visible + char + "…") > width:
            break
        visible += char
    display = visible + "…"
    return display, " " * (width - _cell_width(display))


def format_unread_count(count: int | None) -> str:
    """Return the fixed four-cell unread-count field."""
    count = count or 0
    return "    " if count == 0 else "*** " if count > 999 else f"{count:3d} "


def sort_chats(chats: list[uflChat], query: str = "") -> list[uflChat]:
    """Keep pinned chats first and sort the ordinary subset by recency."""
    pinned = [chat for chat in chats if chat.pinned]
    ordinary = [chat for chat in chats if not chat.pinned]
    if query:
        ordinary = [chat for chat in ordinary if query in chat.name]
    ordinary.sort(key=lambda chat: chat.last_message_time or "", reverse=True)
    return pinned + ordinary


class uflChat(ABC):  # noqa: N801 - public name intentionally follows the design brief
    """A user-facing conversation, either a contact or a group."""

    id: str
    name: str
    last_message_time: str
    unread_count: int | None
    pinned: bool

    @property
    @abstractmethod
    def kind(self) -> str:
        """Return ``contact`` or ``group``."""

    @staticmethod
    def last_time_from_box(box: dict[str, Any] | None) -> str:
        delivered = box.get("lastDeliveredMessageId") if box else None
        millis = delivered.get("deliveredTime") if isinstance(delivered, dict) else None
        if millis in (None, ""):
            return ""
        return datetime.fromtimestamp(int(millis) / 1000, TAIPEI).isoformat()

    @staticmethod
    def unread_from_box(box: dict[str, Any] | None) -> int | None:
        value = box.get("unreadCount") if box else None
        return int(value) if value not in (None, "") else None

    @staticmethod
    def pinned_from_data(*data: dict[str, Any] | None) -> bool:
        for item in data:
            if not item:
                continue
            if item.get("isFavorite") is not None:
                return bool(item["isFavorite"])
            if item.get("favorite") is not None:
                return bool(item["favorite"])
            if item.get("favoriteTimestamp") not in (None, "", 0, "0"):
                return True
        return False

    def to_dict(self, *, full: bool = False) -> dict[str, Any]:
        result = dict(self.__dict__) if full else {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "last_message_time": self.last_message_time,
            "unread_count": self.unread_count,
        }
        result["kind"] = self.kind
        return result


@dataclass
class uflContact(uflChat):  # noqa: N801 - public name intentionally follows the design brief
    id: str
    name: str
    last_message_time: str = ""
    unread_count: int | None = None
    pinned: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return "contact"

    @classmethod
    def from_okline(cls, contact: Contact, box: dict[str, Any]) -> uflContact:
        return cls(
            id=contact.mid,
            name=contact.name,
            last_message_time=cls.last_time_from_box(box),
            unread_count=cls.unread_from_box(box),
            pinned=cls.pinned_from_data(contact.raw, box),
            raw=contact.raw,
        )


@dataclass
class uflGroup(uflChat):  # noqa: N801 - public name intentionally follows the design brief
    id: str
    name: str
    last_message_time: str = ""
    unread_count: int | None = None
    pinned: bool = False
    member_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return "group"

    @classmethod
    def from_okline(cls, group: Group, box: dict[str, Any]) -> uflGroup:
        return cls(
            id=group.chat_mid,
            name=group.name,
            last_message_time=cls.last_time_from_box(box),
            unread_count=cls.unread_from_box(box),
            pinned=cls.pinned_from_data(group.raw, box),
            member_count=group.member_count,
            raw=group.raw,
        )


@dataclass
class uflMessage:  # noqa: N801 - public name intentionally follows the design brief
    id: str = ""
    from_mid: str | None = None
    to: str | None = None
    created_time: int | None = None
    delivered_time: int | None = None
    text: str | None = None
    content_type: int = 0
    availability: str = "unsupported"
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_okline(cls, message: dict[str, Any]) -> uflMessage:
        created = message.get("createdTime")
        delivered = message.get("deliveredTime")
        text = message.get("text")
        content_type = int(message.get("contentType", 0) or 0)
        availability = (
            "readable" if text is not None else
            "encrypted" if message.get("chunks") else "unsupported"
        )
        return cls(
            id=str(message.get("id", "")),
            from_mid=message.get("from"),
            to=message.get("to"),
            created_time=int(created) if created not in (None, "") else None,
            delivered_time=int(delivered) if delivered not in (None, "") else None,
            text=text,
            content_type=content_type,
            availability=availability,
            raw=dict(message),
        )

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.__dict__)
        result["raw"] = dict(self.raw)
        return result

"""Shared user-facing conversation abstraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


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
        """Convert a message-box epoch timestamp to Asia/Taipei ISO-8601."""
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
        """Recognize the favorite fields used by different LINE responses."""
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
        """Return the stable listing representation used by all frontends."""
        result = dict(self.__dict__) if full else {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "last_message_time": self.last_message_time,
            "unread_count": self.unread_count,
        }
        result["kind"] = self.kind
        return result

    # FUTURE: def recent_messages(self, *, limit: int = 50) -> list[uflMessage]: ...
    # FUTURE: def send_text(self, text: str, *, encrypt: bool = True) -> None: ...

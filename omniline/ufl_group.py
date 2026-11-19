"""User-facing group model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from okline.entities import Group

from .ufl_chat import uflChat


@dataclass
class uflGroup(uflChat):  # noqa: N801 - public name intentionally follows the design brief
    """Friendly wrapper around an OkLine group and its chat metadata."""

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

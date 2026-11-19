"""User-facing contact model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from okline.entities import Contact

from .ufl_chat import uflChat


@dataclass
class uflContact(uflChat):  # noqa: N801 - public name intentionally follows the design brief
    """Friendly wrapper around an OkLine contact and its chat metadata."""

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

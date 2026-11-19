"""Normalized user-facing message model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class uflMessage:  # noqa: N801 - public name intentionally follows the design brief
    """A safe, frontend-neutral representation of one wire message."""

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
        if text is not None:
            availability = "readable"
        elif message.get("chunks"):
            availability = "encrypted"
        else:
            availability = "unsupported"
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

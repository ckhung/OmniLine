"""Shared OkLine-backed operations for the three OmniLine frontends."""

from __future__ import annotations

from typing import Any

from okline.entities import Contact, Group

from .ufl import uflChat, uflContact, uflGroup, uflMessage

MESSAGE_BOX_BATCH_SIZE = 100


def message_boxes_by_id(api: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch listing metadata in bounded batches."""
    boxes: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), MESSAGE_BOX_BATCH_SIZE):
        result = api.get_message_boxes_by_ids(
            ids[start : start + MESSAGE_BOX_BATCH_SIZE],
            with_unread_count=True,
            last_messages_count=1,
        )
        assert isinstance(result, dict)
        batch = result.get("messageBoxesByIds", result.get("messageBoxes", {})) or {}
        if isinstance(batch, dict):
            boxes.update({str(mid): box for mid, box in batch.items() if isinstance(box, dict)})
        else:
            boxes.update(
                {
                    str(box["id"]): box
                    for box in batch
                    if isinstance(box, dict) and box.get("id")
                }
            )
    return boxes


def list_chats(api: Any) -> list[uflChat]:
    """Return all contacts and groups as one polymorphic list."""
    contact_ids = api.get_all_contact_ids() or []
    contacts_result = api.get_contacts(contact_ids) if contact_ids else {"contacts": {}}
    assert isinstance(contacts_result, dict)
    contact_data = contacts_result.get("contacts", {})
    assert isinstance(contact_data, dict)
    contact_ids = list(contact_data)

    all_chats = api.get_all_chat_mids() or {}
    group_ids = list(
        dict.fromkeys(
            (all_chats.get("memberChatMids", []) or [])
            + (all_chats.get("invitedChatMids", []) or [])
        )
    )
    group_result = api.get_chats(group_ids) if group_ids else {"chats": []}
    assert isinstance(group_result, dict)
    group_data = group_result.get("chats", [])
    assert isinstance(group_data, list)

    boxes = message_boxes_by_id(api, contact_ids + group_ids)
    chats: list[uflChat] = []
    for mid, data in contact_data.items():
        assert isinstance(data, dict)
        chats.append(uflContact.from_okline(Contact.from_dict(data), boxes.get(mid, {})))
    for data in group_data:
        assert isinstance(data, dict)
        group = Group.from_dict(data)
        chats.append(uflGroup.from_okline(group, boxes.get(group.chat_mid, {})))
    return chats


def find_chats(chats: list[uflChat], needle: str) -> list[uflChat]:
    """Match a substring against the exact MID or display name."""
    return [chat for chat in chats if needle in chat.id or needle in chat.name]


def resolve_contact_names(api: Any, mids: list[str]) -> dict[str, str]:
    """Resolve sender MIDs through ``getContactsV2`` display-name fields."""
    if not mids:
        return {}
    result = api.get_contacts(mids)
    assert isinstance(result, dict)
    contacts = result.get("contacts", {})
    assert isinstance(contacts, dict)
    names: dict[str, str] = {}
    for mid, data in contacts.items():
        assert isinstance(data, dict)
        name = Contact.from_dict(data).name
        if name:
            names[str(mid)] = name
    return names


def refresh_unread_counts(api: Any, chats: list[uflChat]) -> None:
    """Refresh unread counts and latest timestamps without rebuilding chats."""
    boxes = message_boxes_by_id(api, [chat.id for chat in chats])
    for chat in chats:
        box = boxes.get(chat.id, {})
        chat.unread_count = chat.unread_from_box(box)
        chat.last_message_time = chat.last_time_from_box(box)


def mark_chat_read(api: Any, chat: uflChat, messages: list[uflMessage]) -> None:
    """Mark the selected chat read through its newest retrieved message."""
    readable = [message for message in messages if message.id]
    if not readable:
        return
    latest = max(readable, key=lambda message: message.created_time or -1)
    api.send_chat_checked(chat.id, last_message_id=latest.id)
    chat.unread_count = 0


def recent_messages(api: Any, chat: uflChat, count: int) -> list[uflMessage]:
    """Fetch one recent page and decrypt sealed messages when possible."""
    raw_messages = api.get_recent_messages(chat.id, count=count) or []
    messages: list[uflMessage] = []
    for raw in raw_messages:
        assert isinstance(raw, dict)
        decoded = raw
        if raw.get("chunks"):
            try:
                decoded = api.decrypt_message(raw)
            except Exception:
                decoded = dict(raw)
                decoded["text"] = "[encrypted]"
        message = uflMessage.from_okline(decoded)
        if decoded is not raw and decoded.get("text") != "[encrypted]":
            message.raw.pop("chunks", None)
        messages.append(message)
    return messages

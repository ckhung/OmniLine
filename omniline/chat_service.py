"""Shared OkLine-backed operations for the three OmniLine frontends."""

from __future__ import annotations

import json
from typing import Any

from okline.entities import Contact, Group

from .ufl import uflChat, uflContact, uflGroup, uflMessage

MESSAGE_BOX_BATCH_SIZE = 100
MEDIA_TYPES = {1: "image", 2: "video", 3: "audio", 14: "file"}


def sticker_reference(message: uflMessage) -> dict[str, str] | None:
    """Return the public LINE sticker CDN URL and accessible label."""
    if message.content_type != 7:
        return None
    metadata = message.raw.get("contentMetadata", {})
    package_id = metadata.get("STKPKGID") if isinstance(metadata, dict) else None
    sticker_id = metadata.get("STKID") if isinstance(metadata, dict) else None
    if not package_id or not sticker_id:
        return None
    return {
        "package_id": str(package_id),
        "sticker_id": str(sticker_id),
        "url": f"https://stickershop.line-scdn.net/stickershop/v1/sticker/{sticker_id}/ANDROID/sticker.png",
        "alt": str(metadata.get("STKTXT") or "Sticker"),
    }


def message_display_text(message: uflMessage) -> str:
    """Return an informative fallback for non-text messages."""
    metadata = message.raw.get("contentMetadata", {})
    if isinstance(metadata, dict):
        if message.content_type == 22:
            return str(metadata.get("ALT_TEXT") or "[interactive message]")
        if message.content_type == 18:
            return "[chat activity]"
        if message.content_type == 15:
            location = message.raw.get("location") or {}
            if isinstance(location, dict):
                return "📍 " + " — ".join(
                    value for value in (location.get("title"), location.get("address")) if value
                )
            return "[location]"
        if message.content_type == 14:
            return f"[file: {metadata.get('FILE_NAME') or 'file'}]"
    return message.text or f"[{message.availability}]"


def media_reference(message: uflMessage) -> dict[str, str] | None:
    """Return the safe OBS reference for a media message, when available."""
    kind = MEDIA_TYPES.get(message.content_type)
    raw = message.raw
    metadata = raw.get("contentMetadata", {}) if isinstance(raw, dict) else {}
    if not kind or not isinstance(metadata, dict):
        return None
    sid = metadata.get("SID") or metadata.get("sid") or "m"
    oid = metadata.get("OID") or metadata.get("oid") or message.id
    if not isinstance(sid, str) or not isinstance(oid, str) or not sid or not oid:
        return None
    media_info: dict[str, Any] = {}
    encoded_info = metadata.get("MEDIA_CONTENT_INFO")
    if isinstance(encoded_info, str):
        try:
            parsed = json.loads(encoded_info)
            if isinstance(parsed, dict):
                media_info = parsed
        except json.JSONDecodeError:
            pass
    name = metadata.get("FILE_NAME") or metadata.get("fileName") or f"{kind}-{message.id}"
    extension = str(media_info.get("extension") or "").lower()
    mime = metadata.get("MIME_TYPE") or metadata.get("mimeType") or {
        "jpeg": "image/jpeg",
        "jpg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(extension) or {
        "image": "image/*",
        "video": "video/*",
        "audio": "audio/*",
        "file": "application/octet-stream",
    }[kind]
    return {"kind": kind, "sid": sid, "oid": oid, "name": str(name), "mime": str(mime)}


def download_media(api: Any, message: uflMessage) -> tuple[bytes, dict[str, str]]:
    """Download one media message through OkLine's authenticated media path."""
    reference = media_reference(message)
    if reference is None:
        raise ValueError("message has no downloadable media reference")
    if message.raw.get("chunks") and hasattr(api.e2ee, "download_sealed_media"):
        result = api.e2ee.download_sealed_media(message.raw, info=True)
        data, info = result if isinstance(result, tuple) else (result, {})
        if isinstance(info, dict):
            reference["name"] = str(info.get("name") or reference["name"])
            reference["mime"] = str(info.get("mime") or reference["mime"])
        return data, reference
    return api.obs.download_object("talk", reference["sid"], reference["oid"]), reference


def message_boxes_by_id(api: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch listing metadata in bounded batches."""
    boxes: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), MESSAGE_BOX_BATCH_SIZE):
        result = api.get_message_boxes_by_ids(
            ids[start : start + MESSAGE_BOX_BATCH_SIZE],
            with_unread_count=True,
            last_messages_count=1,
        )
        if not isinstance(result, dict):
            raise RuntimeError("LINE returned invalid message-box data")
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
    if not isinstance(contacts_result, dict):
        raise RuntimeError("LINE returned invalid contact data")
    contact_data = contacts_result.get("contacts", {})
    if not isinstance(contact_data, dict):
        raise RuntimeError("LINE returned invalid contact data")
    contact_ids = list(contact_data)

    all_chats = api.get_all_chat_mids() or {}
    group_ids = list(
        dict.fromkeys(
            (all_chats.get("memberChatMids", []) or [])
            + (all_chats.get("invitedChatMids", []) or [])
        )
    )
    group_result = api.get_chats(group_ids) if group_ids else {"chats": []}
    if not isinstance(group_result, dict):
        raise RuntimeError("LINE returned invalid group data")
    group_data = group_result.get("chats", [])
    if not isinstance(group_data, list):
        raise RuntimeError("LINE returned invalid group data")

    boxes = message_boxes_by_id(api, contact_ids + group_ids)
    chats: list[uflChat] = []
    for mid, data in contact_data.items():
        if not isinstance(data, dict):
            raise RuntimeError("LINE returned invalid contact data")
        chats.append(uflContact.from_okline(Contact.from_dict(data), boxes.get(mid, {})))
    for data in group_data:
        if not isinstance(data, dict):
            raise RuntimeError("LINE returned invalid group data")
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
    if not isinstance(result, dict):
        raise RuntimeError("LINE returned invalid contact data")
    contacts = result.get("contacts", {})
    if not isinstance(contacts, dict):
        raise RuntimeError("LINE returned invalid contact data")
    names: dict[str, str] = {}
    for mid, data in contacts.items():
        if not isinstance(data, dict):
            raise RuntimeError("LINE returned invalid contact data")
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
    self_mid = getattr(getattr(api, "tokens", None), "mid", None)
    for raw in raw_messages:
        if not isinstance(raw, dict):
            raise RuntimeError("LINE returned invalid message data")
        decoded = raw
        if raw.get("chunks"):
            try:
                decoded = api.decrypt_message(raw)
            except Exception:
                decoded = dict(raw)
                decoded["text"] = (
                    "[encrypted outgoing message; plaintext unavailable]"
                    if self_mid and raw.get("from") == self_mid
                    else "[encrypted]"
                )
        message = uflMessage.from_okline(decoded)
        if decoded is not raw and decoded.get("text") != "[encrypted]":
            message.raw.pop("chunks", None)
        messages.append(message)
    return messages

"""Deterministic tests for OmniLine's frontend-neutral service layer."""

from __future__ import annotations

import json
from io import StringIO
from types import SimpleNamespace
from typing import Any

from okline.entities import Contact
from omniline.chat_service import (
    download_media,
    find_chats,
    list_chats,
    mark_chat_read,
    media_reference,
    message_display_text,
    recent_messages,
    resolve_contact_names,
    sticker_reference,
)
from omniline.cli import write_history
from omniline.tui import TuiState, _first_url, _wrap_display_cells
from omniline.ufl import repeat_pattern, uflContact, uflGroup, uflMessage

CONTACT_MID = "u" + "1" * 32
GROUP_MID = "c" + "2" * 32


class FakeApi:
    """Small API double: no HTTP, credentials, Node, or OkLine transport."""

    def __init__(self) -> None:
        self.recent_calls: list[tuple[str, int]] = []
        self.decrypt_calls: list[str] = []
        self.checked_calls: list[tuple[str, str]] = []
        self.messages: list[dict] = []
        self.tokens = SimpleNamespace(mid=None)

    def get_all_contact_ids(self) -> list[str]:
        return [CONTACT_MID]

    def get_contacts(self, mids: list[str]) -> dict:
        assert mids == [CONTACT_MID]
        return {"contacts": {CONTACT_MID: {"contact": {
            "mid": CONTACT_MID,
            "displayName": "Alice",
            "displayNameOverridden": "Alice (work)",
        }}}}

    def get_all_chat_mids(self) -> dict:
        return {"memberChatMids": [GROUP_MID], "invitedChatMids": [GROUP_MID]}

    def get_chats(self, mids: list[str]) -> dict:
        assert mids == [GROUP_MID]
        return {"chats": [{
            "chatMid": GROUP_MID,
            "chatName": "Hiking",
            "extra": {"groupExtra": {"memberMids": [CONTACT_MID, "u" + "3" * 32]}},
        }]}

    def get_message_boxes_by_ids(self, ids: list[str], **kwargs: Any) -> Any:
        assert ids == [CONTACT_MID, GROUP_MID]
        return {"messageBoxesByIds": {
            CONTACT_MID: {
                "unreadCount": 2,
                "lastDeliveredMessageId": {"deliveredTime": 1700000000000},
            },
            GROUP_MID: {"unreadCount": 0},
        }}

    def get_recent_messages(self, mid: str, count: int) -> list[dict]:
        self.recent_calls.append((mid, count))
        return self.messages

    def decrypt_message(self, message: dict) -> dict:
        self.decrypt_calls.append(message["id"])
        if message["id"] == "failed":
            raise ValueError("synthetic decryption failure")
        decoded = dict(message)
        decoded["text"] = "decoded"
        return decoded

    def send_chat_checked(self, chat_mid: str, last_message_id: str) -> None:
        self.checked_calls.append((chat_mid, last_message_id))


def test_list_chats_combines_contacts_groups_and_metadata():
    chats = list_chats(FakeApi())

    assert [chat.kind for chat in chats] == ["contact", "group"]
    contact, group = chats
    assert isinstance(contact, uflContact)
    assert contact.name == "Alice (work)"
    assert contact.unread_count == 2
    assert isinstance(group, uflGroup)
    assert group.name == "Hiking"
    assert group.member_count == 2
    assert group.unread_count == 0


def test_find_chats_matches_mid_or_display_name():
    chats = [
        uflContact(CONTACT_MID, "Alice"),
        uflGroup(GROUP_MID, "Hiking"),
    ]

    assert find_chats(chats, "lice") == [chats[0]]
    assert find_chats(chats, GROUP_MID[:5]) == [chats[1]]
    assert find_chats(chats, "missing") == []


def test_find_chats_preserves_ambiguous_matches():
    chats = [uflContact("u1", "Team"), uflGroup("c2", "Team")]

    assert find_chats(chats, "Team") == chats


def test_resolve_contact_names_uses_overridden_display_name():
    assert resolve_contact_names(FakeApi(), [CONTACT_MID]) == {CONTACT_MID: "Alice (work)"}


def test_recent_messages_forwards_count_and_decrypts_sealed_message():
    api = FakeApi()
    api.messages = [{"id": "sealed", "chunks": ["ciphertext"], "contentType": 0}]

    messages = recent_messages(api, uflGroup(GROUP_MID, "Hiking"), 20)

    assert api.recent_calls == [(GROUP_MID, 20)]
    assert api.decrypt_calls == ["sealed"]
    assert messages[0].text == "decoded"
    assert messages[0].availability == "readable"
    assert "chunks" not in messages[0].raw


def test_recent_messages_marks_failed_decryption_without_fabricating_text():
    api = FakeApi()
    api.messages = [{"id": "failed", "chunks": ["ciphertext"], "contentType": 0}]

    messages = recent_messages(api, uflGroup(GROUP_MID, "Hiking"), 1)

    assert messages[0].text == "[encrypted]"
    assert messages[0].availability == "readable"
    assert "chunks" in messages[0].raw


def test_recent_messages_identifies_unrecoverable_outgoing_ciphertext():
    api = FakeApi()
    api.tokens = SimpleNamespace(mid=CONTACT_MID)
    api.messages = [
        {"id": "failed", "from": CONTACT_MID, "chunks": ["ciphertext"], "contentType": 0}
    ]

    message = recent_messages(api, uflContact("u3", "Ruby"), 1)[0]

    assert message.text == "[encrypted outgoing message; plaintext unavailable]"


def test_marking_chat_read_uses_newest_message_id():
    api = FakeApi()
    chat = uflGroup(GROUP_MID, "Hiking", unread_count=2)
    api.messages = [
        {"id": "old", "createdTime": "100", "text": "old"},
        {"id": "new", "createdTime": "200", "text": "new"},
    ]
    messages = recent_messages(api, chat, 2)
    messages[0].created_time = 100
    messages[1].created_time = 200

    mark_chat_read(api, chat, messages)

    assert api.checked_calls == [(GROUP_MID, messages[1].id)]
    assert chat.unread_count == 0


def test_chat_to_dict_has_short_and_full_forms():
    chat = uflGroup(GROUP_MID, "Hiking", member_count=2, raw={"chatName": "Hiking"})

    short = chat.to_dict()
    full = chat.to_dict(full=True)

    assert set(short) == {"id", "name", "kind", "last_message_time", "unread_count"}
    assert full["member_count"] == 2
    assert full["raw"] == {"chatName": "Hiking"}


def test_message_box_timestamp_is_taipei_iso8601():
    chat = uflContact.from_okline(
        Contact.from_dict({"mid": CONTACT_MID, "displayName": "Alice"}),
        {"lastDeliveredMessageId": {"deliveredTime": 1700000000000}},
    )

    assert chat.last_message_time.endswith("+08:00")


def test_message_json_is_serializable():
    api = FakeApi()
    api.messages = [{"id": "plain", "text": "hello", "contentType": 0}]

    message = recent_messages(api, uflContact(CONTACT_MID, "Alice"), 1)[0]

    assert json.loads(json.dumps(message.to_dict(), ensure_ascii=False))["text"] == "hello"


def test_media_reference_and_download_use_observable_metadata():
    message = uflMessage(
        id="media-1",
        content_type=1,
        raw={"contentMetadata": {"SID": "m", "OID": "object-1", "FILE_NAME": "x.jpg"}},
    )
    reference = media_reference(message)
    assert reference == {
        "kind": "image",
        "sid": "m",
        "oid": "object-1",
        "name": "x.jpg",
        "mime": "image/*",
    }

    class Obs:
        def download_object(self, service: str, sid: str, oid: str) -> bytes:
            assert (service, sid, oid) == ("talk", "m", "object-1")
            return b"image-bytes"

    api = SimpleNamespace(obs=Obs())
    assert download_media(api, message) == (b"image-bytes", reference)

    inferred = uflMessage(
        id="media-2",
        content_type=1,
        raw={"contentMetadata": {"MEDIA_CONTENT_INFO": '{"extension":"jpeg"}'}},
    )
    assert media_reference(inferred) == {
        "kind": "image",
        "sid": "m",
        "oid": "media-2",
        "name": "image-media-2",
        "mime": "image/jpeg",
    }

    sticker = uflMessage(
        id="sticker-1",
        content_type=7,
        raw={"contentMetadata": {"STKPKGID": "11538", "STKID": "51626501"}},
    )
    sticker_url = sticker_reference(sticker)
    assert sticker_url is not None
    assert sticker_url["url"].endswith("/51626501/ANDROID/sticker.png")
    flex = uflMessage(
        id="flex-1",
        content_type=22,
        raw={"contentMetadata": {"ALT_TEXT": "calendar activity"}},
    )
    assert message_display_text(flex) == "calendar activity"


def test_cli_history_does_not_emit_raw_backend_payloads():
    message = uflMessage(id="m1", text="hello", raw={"chunks": ["ciphertext"]})
    output = StringIO()

    write_history([message], output)

    assert "chunks" not in output.getvalue()


def test_empty_chat_list_ignores_content_and_profile_activation():
    state = TuiState(api=FakeApi(), chats=[])

    state.enter_content()
    state.enter_profile()

    assert state.mode == "list"
    assert state.selected_chat is None


def test_repeat_pattern_fills_requested_display_width():
    assert repeat_pattern("* ", 7) == "* * * *"
    assert repeat_pattern("ab", 5) == "ababa"


def test_own_message_wrapping_uses_terminal_display_cells():
    assert _wrap_display_cells("abcdef", 4) == ["abcd", "ef"]
    assert _wrap_display_cells("你好世界", 6) == ["你好世", "界"]


def test_first_url_uses_the_first_url_and_removes_sentence_punctuation():
    assert _first_url("See https://example.com/a, then https://ignored.example") == (
        "https://example.com/a"
    )
    assert _first_url("no URL here") is None


def test_filtered_list_can_be_restored_and_counts_refreshed():
    state = TuiState(api=FakeApi(), chats=list_chats(FakeApi()))
    state.query = "missing"
    state.cursor = 0

    state.restore_full_list()
    state.refresh_counts()

    assert state.query == ""
    assert state.chats[0].unread_count == 2

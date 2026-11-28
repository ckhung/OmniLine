"""Accessible, server-rendered Flask web frontend for OmniLine."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, render_template, request
from markupsafe import Markup, escape
from werkzeug.middleware.proxy_fix import ProxyFix

from okline.entities import Contact
from okline.exceptions import LineApiError

from .chat_service import (
    download_media,
    list_chats,
    mark_chat_read,
    media_reference,
    message_display_text,
    recent_messages,
    resolve_contact_names,
    sticker_reference,
)
from .config import load_config
from .paths import expand_user_path, user_home
from .ufl import TAIPEI, sort_chats, uflChat, uflContact
from .web_auth import initialize_auth, install_auth

URL_PATTERN = re.compile(r"https?://[^\s<]+")


def _error_logger(path: Path) -> logging.Logger:
    """Create a private per-application error logger."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(descriptor)
    path.chmod(0o600)
    logger = logging.getLogger(f"omniline.web.error.{id(path)}.{id(object())}")
    logger.setLevel(logging.ERROR)
    logger.propagate = False
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


def create_app(
    auth_dir: str | Path | None = None,
    password_file: str | Path | None = None,
    *,
    api: Any | None = None,
    tokens_file: str | Path | None = None,
) -> Flask:
    """Create the authenticated OmniLine web application."""
    app = Flask(__name__)
    tokens_file = tokens_file or os.environ.get("OMNILINE_TOKENS_FILE")
    password_file = password_file or os.environ.get("OMNILINE_PASSWORD_FILE")
    error_log = (
        expand_user_path(auth_dir) if auth_dir else user_home() / ".omniline"
    ) / "error.log"
    error_logger = _error_logger(error_log)
    config = load_config()
    auth_root = expand_user_path(auth_dir) if auth_dir else None
    access_log = auth_root / "access.log" if auth_root else config.web_access_log
    install_auth(
        app,
        auth_dir,
        password_file,
        access_log=access_log,
        max_failed_attempts=config.web_max_failed_attempts,
        failed_attempt_window_seconds=config.web_failed_attempt_window_seconds,
        session_lifetime_hours=config.web_session_lifetime_hours,
    )
    app.config["OMNILINE_API"] = api
    app.config["OMNILINE_INJECTED_API"] = api is not None

    def backend() -> Any:
        selected = app.config.get("OMNILINE_API")
        if selected is not None and app.config.get("OMNILINE_INJECTED_API"):
            return selected
        from okline import OkLine

        config = load_config()
        configured_tokens = tokens_file or os.environ.get("OMNILINE_TOKENS_FILE")
        selected_path = (
            expand_user_path(configured_tokens) if configured_tokens else config.tokens_file
        )
        token_mtime = selected_path.stat().st_mtime_ns
        if (
            selected is not None
            and app.config.get("OMNILINE_TOKENS_FILE") == str(selected_path)
            and app.config.get("OMNILINE_TOKENS_MTIME") == token_mtime
        ):
            return selected
        if selected is not None:
            selected.close()
        app.config["OMNILINE_TOKENS_FILE"] = str(selected_path)
        app.config["OMNILINE_TOKENS_MTIME"] = token_mtime
        selected = OkLine.from_tokens_file(str(selected_path))
        if not getattr(selected.tokens, "access_token", None):
            raise RuntimeError("session file has no access token; log in again")
        app.config["OMNILINE_API"] = selected
        return selected

    def chats() -> list[uflChat]:
        result = list_chats(backend())
        config = load_config()
        for chat in result:
            chat.pinned = chat.id in config.pinned_chats
        return sort_chats(result)

    def selected_chat(chat_id: str, available: list[uflChat]) -> uflChat:
        selected = next((chat for chat in available if chat.id == chat_id), None)
        if selected is None and chat_id[:1].lower() == "u":
            result = backend().get_contacts([chat_id])
            data = (result.get("contacts", {}) if isinstance(result, dict) else {}).get(chat_id)
            if isinstance(data, dict):
                selected = uflContact.from_okline(Contact.from_dict(data), {})
        if selected is None:
            abort(404)
        return selected

    def member_mids(chat: uflChat) -> list[str]:
        raw = getattr(chat, "raw", {})
        extra = raw.get("extra", {}) if isinstance(raw, dict) else {}
        group_extra = extra.get("groupExtra", {}) if isinstance(extra, dict) else {}
        values = group_extra.get("memberMids", []) if isinstance(group_extra, dict) else []
        return [str(mid) for mid in values if mid]

    def service_error(exc: Exception, *, media: bool = False):
        api = app.config.get("OMNILINE_API")
        tokens = getattr(api, "tokens", None)
        token_path = app.config.get("OMNILINE_TOKENS_FILE", "<not loaded>")
        try:
            token_stat = Path(token_path).stat()
            token_file_state = {
                "exists": True,
                "mode": oct(token_stat.st_mode & 0o777),
                "size": token_stat.st_size,
            }
        except OSError:
            token_file_state = {"exists": False}
        error_logger.exception(
            "web request failed path=%s tokens_file=%s token_file_state=%s "
            "access_token_present=%s refresh_token_present=%s mid_present=%s "
            "token_issue_time=%s okline_module=%s",
            request.path,
            token_path,
            token_file_state,
            bool(getattr(tokens, "access_token", None)),
            bool(getattr(tokens, "refresh_token", None)),
            bool(getattr(tokens, "mid", None)),
            getattr(tokens, "token_issue_time_epoch_sec", None),
            getattr(getattr(api, "__class__", None), "__module__", None),
        )
        return ("media unavailable", 404) if media else (render_template("error.html"), 503)

    @app.context_processor
    def common_template_values() -> dict[str, Any]:
        return {
            "theme": "light" if request.args.get("theme") == "light" else "dark",
            "message_display_text": message_display_text,
        }

    @app.route("/")
    def index():
        try:
            return render_template("index.html", chats=chats(), selected=None)
        except (LineApiError, OSError, RuntimeError, ValueError) as exc:
            return service_error(exc)

    @app.route("/chat/<path:chat_id>")
    def chat_view(chat_id: str):
        try:
            available = chats()
            selected = selected_chat(chat_id, available)
            messages = list(recent_messages(backend(), selected, load_config().history_limit))
            messages.sort(key=lambda message: message.created_time or -1)
            unread_count = selected.unread_count or 0
            mark_chat_read(backend(), selected, messages)
            mids = sorted({message.from_mid for message in messages if message.from_mid})
            names = resolve_contact_names(backend(), mids)
            media_messages = {
                message.id: media_reference(message)
                for message in messages
                if media_reference(message) is not None
            }
            sticker_messages = {
                message.id: sticker_reference(message)
                for message in messages
                if sticker_reference(message) is not None
            }
            return render_template(
                "index.html", chats=available, selected=selected,
                messages=messages, sender_names=names, media_messages=media_messages,
                sticker_messages=sticker_messages, unread_count=unread_count,
            )
        except (LineApiError, OSError, RuntimeError, ValueError) as exc:
            return service_error(exc)

    @app.route("/chat/<path:chat_id>/profile")
    def chat_profile(chat_id: str):
        try:
            available = chats()
            selected = selected_chat(chat_id, available)
            return render_template(
                "index.html", chats=available, selected=selected, view_mode="profile"
            )
        except (LineApiError, OSError, RuntimeError, ValueError) as exc:
            return service_error(exc)

    @app.route("/chat/<path:chat_id>/members")
    def chat_members(chat_id: str):
        try:
            available = chats()
            selected = selected_chat(chat_id, available)
            if selected.kind != "group":
                abort(404)
            mids = member_mids(selected)
            names = resolve_contact_names(backend(), mids) if mids else {}
            return render_template(
                "index.html",
                chats=available,
                selected=selected,
                view_mode="members",
                member_mids=mids,
                member_names=names,
            )
        except (LineApiError, OSError, RuntimeError, ValueError) as exc:
            return service_error(exc)

    @app.route("/media/<path:chat_id>/<message_id>")
    def media_view(chat_id: str, message_id: str):
        """Serve one authorized media object; never trust client paths."""
        try:
            selected = next((chat for chat in chats() if chat.id == chat_id), None)
            if selected is None:
                abort(404)
            messages = recent_messages(backend(), selected, load_config().history_limit)
            message = next((item for item in messages if item.id == message_id), None)
            if message is None:
                abort(404)
            data, reference = download_media(backend(), message)
            mime = reference["mime"]
            if mime.endswith("/*"):
                mime = {
                    "image/*": "image/jpeg",
                    "video/*": "video/mp4",
                    "audio/*": "audio/mpeg",
                }.get(mime, "application/octet-stream")
            return Response(data, mimetype=mime, headers={"Content-Disposition": "inline"})
        except (LineApiError, OSError, RuntimeError, ValueError) as exc:
            return service_error(exc, media=True)

    @app.template_filter("message_time")
    def message_time(value: int | None) -> str:
        if value is None:
            return ""
        return datetime.fromtimestamp(value / 1000, TAIPEI).strftime("%m-%d %H:%M")

    @app.template_filter("chat_time")
    def chat_time(value: str) -> str:
        return value[5:16].replace("T", " ") if value else ""

    @app.template_filter("linkify")
    def linkify(value: str) -> Markup:
        """Turn message URLs into safe, clickable links."""
        parts: list[str] = []
        position = 0
        for match in URL_PATTERN.finditer(value):
            parts.append(str(escape(value[position : match.start()])))
            url = match.group(0).rstrip(".,;:!?)]}")
            trailing = match.group(0)[len(url) :]
            href = str(escape(url))
            label = str(escape(url))
            parts.append(
                f'<a href="{href}" rel="noopener noreferrer" target="_blank">{label}</a>'
            )
            parts.append(str(escape(trailing)))
            position = match.end()
        parts.append(str(escape(value[position:])))
        return Markup("".join(parts))

    app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
        app.wsgi_app, x_for=1, x_prefix=1, x_proto=1
    )
    return app


def run_web(
    host: str = "127.0.0.1",
    port: int = 1730,
    *,
    init_auth: bool = False,
    password_file: str | Path | None = None,
    tokens_file: str | Path | None = None,
) -> int:
    """Run the development server or initialize web authentication."""
    if init_auth:
        initialize_auth()
        return 0
    create_app(password_file=password_file, tokens_file=tokens_file).run(host=host, port=port)
    return 0


app = create_app()

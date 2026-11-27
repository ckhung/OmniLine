"""Accessible, server-rendered Flask web frontend for OmniLine."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, render_template, request
from markupsafe import Markup, escape
from werkzeug.middleware.proxy_fix import ProxyFix

from .chat_service import (
    download_media,
    list_chats,
    media_reference,
    message_display_text,
    recent_messages,
    resolve_contact_names,
    sticker_reference,
)
from .config import load_config
from .ufl import TAIPEI, sort_chats, uflChat
from .web_auth import initialize_auth, install_auth

URL_PATTERN = re.compile(r"https?://[^\s<]+")


def create_app(
    auth_dir: str | Path | None = None,
    password_file: str | Path | None = None,
    *,
    api: Any | None = None,
) -> Flask:
    """Create the authenticated OmniLine web application."""
    app = Flask(__name__)
    config = load_config()
    auth_root = Path(auth_dir).expanduser() if auth_dir else None
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

    def backend() -> Any:
        selected = app.config.get("OMNILINE_API")
        if selected is not None:
            return selected
        from okline import OkLine

        config = load_config()
        selected = OkLine.from_tokens_file(str(config.tokens_file))
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
        except (OSError, RuntimeError, ValueError):
            return render_template("error.html"), 503

    @app.route("/chat/<path:chat_id>")
    def chat_view(chat_id: str):
        try:
            available = chats()
            selected = next((chat for chat in available if chat.id == chat_id), None)
            if selected is None:
                abort(404)
            messages = list(recent_messages(backend(), selected, load_config().history_limit))
            messages.sort(key=lambda message: message.created_time or -1)
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
                sticker_messages=sticker_messages,
            )
        except (OSError, RuntimeError, ValueError):
            return render_template("error.html"), 503

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
        except (OSError, RuntimeError, ValueError):
            return "media unavailable", 404

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
) -> int:
    """Run the development server or initialize web authentication."""
    if init_auth:
        initialize_auth()
        return 0
    create_app(password_file=password_file).run(host=host, port=port)
    return 0


app = create_app()

"""Password and session authentication for the web frontend."""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from flask import Flask, redirect, request, session, url_for

from .paths import expand_user_path, user_home

AUTH_DIR_ENV = "OMNILINE_AUTH_DIR"
PASSWORD_FILE = "password.hash"
SESSION_SECRET_FILE = "session.secret"
SALT_BYTES = 16
KEY_BYTES = 32
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def _auth_dir(path: str | Path | None = None) -> Path:
    configured = path or os.environ.get(AUTH_DIR_ENV)
    return expand_user_path(configured) if configured else user_home() / ".omniline"


def _private_file(path: Path) -> bytes:
    """Read a secret file owned by this user with mode 0600."""
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"{path.name} is not a regular file")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f"{path} must be owned by this user and have mode 0600")
    return path.read_bytes().strip()


def hash_password(password: str) -> str:
    """Return a self-contained scrypt password-verifier string."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_BYTES,
    )
    fields = [
        "scrypt",
        str(SCRYPT_N),
        str(SCRYPT_R),
        str(SCRYPT_P),
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    ]
    return "$".join(fields)


def verify_password(password: str, encoded: bytes) -> bool:
    """Verify a password without revealing whether a field matched."""
    try:
        algorithm, n, r, p, salt_text, expected_text = encoded.decode("ascii").split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, UnicodeError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _load_auth_files(directory: Path, password_file: Path | None = None) -> tuple[bytes, bytes]:
    password_hash = _private_file(password_file or directory / PASSWORD_FILE)
    session_secret = _private_file(directory / SESSION_SECRET_FILE)
    if not password_hash or not session_secret:
        raise RuntimeError(f"{directory} contains an empty authentication file")
    return password_hash, session_secret


def _ensure_private_file(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        _private_file(path)
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)


def _failed_attempts(path: Path, window: int) -> int:
    cutoff = time.time() - window
    failures = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
            timestamp = float(record["timestamp"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if timestamp < cutoff:
            continue
        if record.get("result") == "success":
            failures = 0
        elif record.get("result") == "failure":
            failures += 1
    return failures


def _log_attempt(path: Path, result: str, remote_addr: str | None) -> None:
    record = {
        "timestamp": time.time(),
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "result": result,
        "remote_addr": remote_addr or "-",
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, separators=(",", ":")) + "\n")


def install_auth(
    app: Flask,
    auth_dir: str | Path | None = None,
    password_file: str | Path | None = None,
    *,
    access_log: str | Path,
    max_failed_attempts: int,
    failed_attempt_window_seconds: int,
    session_lifetime_hours: int,
) -> None:
    """Install the password gate and authentication routes on a Flask app."""
    directory = _auth_dir(auth_dir)
    selected_password_file = expand_user_path(password_file) if password_file else None
    access_log_path = expand_user_path(access_log)
    _ensure_private_file(access_log_path)
    try:
        app.secret_key = _private_file(directory / SESSION_SECRET_FILE)
    except FileNotFoundError:
        pass
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("OMNILINE_COOKIE_SECURE") == "1",
        SESSION_COOKIE_NAME="__Host-omniline_session"
        if os.environ.get("OMNILINE_COOKIE_SECURE") == "1"
        else "omniline_session",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=session_lifetime_hours),
        SESSION_REFRESH_EACH_REQUEST=False,
    )

    @app.before_request
    def authenticate() -> Any:
        if app.secret_key is None:
            try:
                app.secret_key = _private_file(directory / SESSION_SECRET_FILE)
            except (OSError, RuntimeError):
                return "authentication is not configured", 503
        if request.endpoint in {"login", "static"}:
            return None
        if not session.get("authenticated", False):
            return redirect(url_for("login"))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            # Deployment invariant: one Gunicorn worker serves this application.
            # The access log is therefore the single shared rate-limit state.
            # If multiple workers are introduced, use a transactional shared
            # store such as SQLite or Redis instead of parsing this file.
            if _failed_attempts(access_log_path, failed_attempt_window_seconds) >= max_failed_attempts:
                _log_attempt(access_log_path, "blocked", request.remote_addr)
                return redirect(url_for("login", blocked="1"), code=303)
            password_hash, _ = _load_auth_files(directory, selected_password_file)
            if verify_password(request.form.get("password", ""), password_hash):
                _log_attempt(access_log_path, "success", request.remote_addr)
                session.clear()
                session.permanent = True
                session["authenticated"] = True
                return redirect(url_for("index"))
            _log_attempt(access_log_path, "failure", request.remote_addr)
            return redirect(url_for("login", failed="1"), code=303)
        message = ""
        if request.args.get("blocked"):
            message = "too many failed login attempts"
        elif request.args.get("failed"):
            message = "login failed"
        notice = f'<p role="alert">{message}</p>' if message else ""
        return (
            "<!doctype html><title>OmniLine login</title>"
            f"{notice}"
            '<form method="post"><label>Password '
            '<input type="password" name="password" autofocus></label> '
            '<button type="submit">Log in</button></form>'
        )

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))


def initialize_auth(directory: str | Path | None = None) -> None:
    """Interactively create the two local authentication files."""
    target = _auth_dir(directory)
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    password = getpass.getpass("OmniLine web password: ")
    confirmation = getpass.getpass("Repeat password: ")
    if not password or not hmac.compare_digest(password, confirmation):
        raise SystemExit("passwords are empty or do not match")
    (target / PASSWORD_FILE).write_text(hash_password(password) + "\n", encoding="ascii")
    (target / SESSION_SECRET_FILE).write_bytes(secrets.token_bytes(32))
    for name in (PASSWORD_FILE, SESSION_SECRET_FILE):
        (target / name).chmod(0o600)
    print(f"created {target / PASSWORD_FILE}")
    print(f"created {target / SESSION_SECRET_FILE}")

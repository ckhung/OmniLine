"""Minimal authenticated Flask frontend for OmniLine."""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import os
import secrets
import stat
from pathlib import Path

from flask import Flask, redirect, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

AUTH_DIR_ENV = "OMNILINE_AUTH_DIR"
PASSWORD_FILE = "password.hash"
SESSION_SECRET_FILE = "session.secret"
SALT_BYTES = 16
KEY_BYTES = 32
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def _auth_dir(path: str | Path | None = None) -> Path:
    return Path(path or os.environ.get(AUTH_DIR_ENV, "~/.omniline")).expanduser()


def _private_file(path: Path) -> bytes:
    """Read a root-owned-by-this-user secret file with restrictive mode."""
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


def _load_auth_files(directory: Path) -> tuple[bytes, bytes]:
    password_hash = _private_file(directory / PASSWORD_FILE)
    session_secret = _private_file(directory / SESSION_SECRET_FILE)
    if not password_hash or not session_secret:
        raise RuntimeError(f"{directory} contains an empty authentication file")
    return password_hash, session_secret


def create_app(auth_dir: str | Path | None = None) -> Flask:
    """Create the Flask app; authentication files are loaded per request."""
    app = Flask(__name__)
    directory = _auth_dir(auth_dir)
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
    )

    @app.before_request
    def authenticate():
        if app.secret_key is None:
            try:
                app.secret_key = _private_file(directory / SESSION_SECRET_FILE)
            except (OSError, RuntimeError):
                return "authentication is not configured", 503
        if request.endpoint in {"login", "static"}:
            return
        if not session.get("authenticated", False):
            return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            password_hash, _ = _load_auth_files(directory)
            if verify_password(request.form.get("password", ""), password_hash):
                session.clear()
                session["authenticated"] = True
                return redirect(url_for("index"))
            return "login failed", 401
        return (
            "<!doctype html><title>OmniLine login</title>"
            '<form method="post"><label>Password '
            '<input type="password" name="password" autofocus></label> '
            '<button type="submit">Log in</button></form>'
        )

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def index():
        return "omniline web"

    return app


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


def run_web(host: str = "127.0.0.1", port: int = 1730, *, init_auth: bool = False) -> int:
    """Run the development server or initialize web authentication."""
    if init_auth:
        initialize_auth()
        return 0
    app.run(host=host, port=port)
    return 0


app = create_app()
app.wsgi_app = ProxyFix(app.wsgi_app, x_prefix=1)  # type: ignore[method-assign]

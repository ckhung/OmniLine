import json
from pathlib import Path

from omniline.__main__ import _build_parser
from omniline.web import create_app
from omniline.web_auth import hash_password, verify_password


def _auth_files(directory: Path, password: str) -> None:
    (directory / "password.hash").write_text(hash_password(password) + "\n", encoding="ascii")
    (directory / "session.secret").write_bytes(b"session-secret-for-tests")
    for path in directory.iterdir():
        path.chmod(0o600)


def test_password_hash_verification() -> None:
    encoded = hash_password("correct")

    assert verify_password("correct", encoded.encode("ascii"))
    assert not verify_password("wrong", encoded.encode("ascii"))
    assert not verify_password("correct", b"not-a-valid-hash")


def test_login_rejects_wrong_password_and_accepts_correct_password(tmp_path: Path) -> None:
    _auth_files(tmp_path, "correct")
    app = create_app(tmp_path)
    assert app.config["PERMANENT_SESSION_LIFETIME"].total_seconds() == 24 * 60 * 60
    client = app.test_client()

    response = client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"

    response = client.post("/login", data={"password": "wrong"})
    assert response.status_code == 303
    assert client.get(response.headers["Location"]).status_code == 200

    response = client.post("/login", data={"password": "correct"})
    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert "Expires=" in response.headers["Set-Cookie"]
    assert client.get("/").status_code == 200
    assert b"OmniLine" in client.get("/").data


def test_temporary_password_file_overrides_default(tmp_path: Path) -> None:
    _auth_files(tmp_path, "default")
    temporary = tmp_path / "temporary-password.hash"
    temporary.write_text(hash_password("temporary") + "\n", encoding="ascii")
    temporary.chmod(0o600)
    client = create_app(tmp_path, password_file=temporary).test_client()

    assert client.post("/login", data={"password": "default"}).status_code == 303
    assert client.post("/login", data={"password": "temporary"}).status_code == 302


def test_failed_attempts_are_logged_and_limited(tmp_path: Path) -> None:
    _auth_files(tmp_path, "correct")
    client = create_app(tmp_path).test_client()

    responses = [client.post("/login", data={"password": "wrong"}) for _ in range(6)]

    assert [response.status_code for response in responses] == [303, 303, 303, 303, 303, 303]
    records = [json.loads(line) for line in (tmp_path / "access.log").read_text().splitlines()]
    assert not (tmp_path / "omniline.db").exists()
    assert [record["result"] for record in records] == [
        "failure",
        "failure",
        "failure",
        "failure",
        "failure",
        "blocked",
    ]


def test_web_parser_accepts_temporary_password_file() -> None:
    args = _build_parser().parse_args(["web", "--password-file", "/tmp/password.hash"])

    assert args.frontend == "web"
    assert args.password_file == "/tmp/password.hash"

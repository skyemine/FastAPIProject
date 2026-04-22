from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from main import create_app


def build_settings(tmp_path) -> Settings:
    return Settings(
        app_env="test",
        app_name="PulseChat Test",
        database_url=f"sqlite:///{tmp_path / 'pulsechat-dm.db'}",
        secret_key="test-secret-key-12345",
        session_cookie_name="pulsechat_session",
        session_max_age_seconds=3600,
        cookie_secure=False,
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
        allowed_origins=[],
        force_https=False,
        auth_rate_limit_count=20,
        auth_rate_limit_window_seconds=60,
        message_rate_limit_count=50,
        message_rate_limit_window_seconds=10,
        message_history_limit=50,
        hsts_max_age_seconds=3600,
        push_public_key="",
        push_private_key="",
        push_subject="mailto:test@example.com",
    )


def register(client: TestClient, username: str, display_name: str) -> None:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "Supersecure123", "display_name": display_name},
    )
    assert response.status_code == 201


def test_frontend_and_auth_flow(tmp_path) -> None:
    app = create_app(settings=build_settings(tmp_path))

    with TestClient(app) as client:
        frontend_response = client.get("/")
        assert frontend_response.status_code == 200
        assert "Prism" in frontend_response.text

        session_response = client.get("/api/session")
        assert session_response.status_code == 200
        assert session_response.json()["authenticated"] is False

        register(client, "alice_dev", "Alice")

        session_response = client.get("/api/session")
        assert session_response.json()["authenticated"] is True
        assert session_response.json()["user"]["username"] == "alice_dev"


def test_session_persists_across_app_restart(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings=settings)

    with TestClient(app) as client:
        register(client, "restart_dev", "Restart")
        session_cookie = client.cookies.get(settings.session_cookie_name)
        assert session_cookie

    restarted_app = create_app(settings=settings)
    with TestClient(restarted_app) as restarted_client:
        restarted_client.cookies.set(settings.session_cookie_name, session_cookie)
        session_response = restarted_client.get("/api/session")
        assert session_response.status_code == 200
        assert session_response.json()["authenticated"] is True
        assert session_response.json()["user"]["username"] == "restart_dev"


def test_two_clients_keep_separate_sessions_on_same_server(tmp_path) -> None:
    app = create_app(settings=build_settings(tmp_path))

    with TestClient(app) as alice_client, TestClient(app) as bob_client:
        register(alice_client, "alice_multi", "Alice")
        register(bob_client, "bob_multi", "Bob")

        alice_session = alice_client.get("/api/session").json()
        bob_session = bob_client.get("/api/session").json()

        assert alice_session["authenticated"] is True
        assert bob_session["authenticated"] is True
        assert alice_session["user"]["username"] == "alice_multi"
        assert bob_session["user"]["username"] == "bob_multi"

        alice_client.post("/api/auth/logout")
        alice_after_logout = alice_client.get("/api/session").json()
        bob_still_signed_in = bob_client.get("/api/session").json()

        assert alice_after_logout["authenticated"] is False
        assert bob_still_signed_in["authenticated"] is True
        assert bob_still_signed_in["user"]["username"] == "bob_multi"


def test_friend_request_accept_and_direct_chat(tmp_path) -> None:
    app = create_app(settings=build_settings(tmp_path))

    with TestClient(app) as alice_client:
        register(alice_client, "alice_dev", "Alice")
        send_request = alice_client.post("/api/friend-requests", json={"username": "bob_dev"})
        assert send_request.status_code == 404

    with TestClient(app) as bob_client:
        register(bob_client, "bob_dev", "Bob")

    with TestClient(app) as alice_client:
        login = alice_client.post("/api/auth/login", json={"username": "alice_dev", "password": "supersecure123"})
        if login.status_code != 200:
            login = alice_client.post("/api/auth/login", json={"username": "alice_dev", "password": "Supersecure123"})
        assert login.status_code == 200
        send_request = alice_client.post("/api/friend-requests", json={"username": "bob_dev"})
        assert send_request.status_code == 201

    with TestClient(app) as bob_client:
        login = bob_client.post("/api/auth/login", json={"username": "bob_dev", "password": "supersecure123"})
        if login.status_code != 200:
            login = bob_client.post("/api/auth/login", json={"username": "bob_dev", "password": "Supersecure123"})
        assert login.status_code == 200
        requests_response = bob_client.get("/api/friend-requests")
        assert requests_response.status_code == 200
        request_id = requests_response.json()[0]["id"]
        accept_response = bob_client.post(f"/api/friend-requests/{request_id}/accept")
        assert accept_response.status_code == 200
        friends_response = bob_client.get("/api/friends")
        assert friends_response.status_code == 200
        assert friends_response.json()[0]["username"] == "alice_dev"

    with TestClient(app) as alice_client:
        login = alice_client.post("/api/auth/login", json={"username": "alice_dev", "password": "supersecure123"})
        if login.status_code != 200:
            login = alice_client.post("/api/auth/login", json={"username": "alice_dev", "password": "Supersecure123"})
        assert login.status_code == 200
        with alice_client.websocket_connect("/ws/direct/bob_dev") as websocket:
            history_payload = websocket.receive_json()
            assert history_payload["type"] == "history"
            assert history_payload["friend"]["username"] == "bob_dev"
            websocket.send_json({"content": "Hello Bob"})
            message_payload = websocket.receive_json()
            assert message_payload["type"] == "message"
            assert message_payload["message"]["content"] == "Hello Bob"

        history_response = alice_client.get("/api/direct/bob_dev/messages")
        assert history_response.status_code == 200
        assert history_response.json()[0]["content"] == "Hello Bob"


def test_rejects_weak_passwords(tmp_path) -> None:
    app = create_app(settings=build_settings(tmp_path))

    with TestClient(app) as client:
        response = client.post(
            "/api/auth/register",
            json={"username": "weak_dev", "password": "weakpass", "display_name": "Weak"},
        )
        assert response.status_code == 422
        assert "Password" in response.json()["detail"]


def test_file_upload_and_download_between_friends(tmp_path) -> None:
    app = create_app(settings=build_settings(tmp_path))

    with TestClient(app) as alice_client:
        register(alice_client, "alice_file", "Alice")

    with TestClient(app) as bob_client:
        register(bob_client, "bob_file", "Bob")

    with TestClient(app) as alice_client:
        alice_client.post("/api/auth/login", json={"username": "alice_file", "password": "supersecure123"})
        alice_client.post("/api/auth/login", json={"username": "alice_file", "password": "Supersecure123"})
        request_response = alice_client.post("/api/friend-requests", json={"username": "bob_file"})
        assert request_response.status_code == 201

    with TestClient(app) as bob_client:
        bob_client.post("/api/auth/login", json={"username": "bob_file", "password": "Supersecure123"})
        request_id = bob_client.get("/api/friend-requests").json()[0]["id"]
        accept_response = bob_client.post(f"/api/friend-requests/{request_id}/accept")
        assert accept_response.status_code == 200

    with TestClient(app) as alice_client:
        alice_client.post("/api/auth/login", json={"username": "alice_file", "password": "Supersecure123"})
        upload_response = alice_client.post(
            "/api/direct/bob_file/files",
            files={"file": ("hello.txt", b"hello from alice", "text/plain")},
        )
        assert upload_response.status_code == 201
        payload = upload_response.json()
        assert payload["attachment_name"] == "hello.txt"
        assert payload["attachment_url"]

        download_response = alice_client.get(payload["attachment_url"])
        assert download_response.status_code == 200
        assert download_response.content == b"hello from alice"


def test_avatar_upload_persists_in_session(tmp_path) -> None:
    app = create_app(settings=build_settings(tmp_path))

    with TestClient(app) as client:
        register(client, "avatar_dev", "Avatar User")
        upload_response = client.post(
            "/api/users/me/avatar",
            files={"file": ("avatar.png", b"\x89PNG\r\n\x1a\navatar-data", "image/png")},
        )
        assert upload_response.status_code == 200
        payload = upload_response.json()
        assert payload["avatar_url"]

        session_response = client.get("/api/session")
        assert session_response.status_code == 200
        assert session_response.json()["user"]["avatar_url"] == payload["avatar_url"]

        avatar_response = client.get(payload["avatar_url"])
        assert avatar_response.status_code == 200


def test_avatar_persists_across_app_restart(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings=settings)

    with TestClient(app) as client:
        register(client, "avatar_restart", "Avatar Restart")
        upload_response = client.post(
            "/api/users/me/avatar",
            files={"file": ("avatar.png", b"\x89PNG\r\n\x1a\navatar-data", "image/png")},
        )
        assert upload_response.status_code == 200
        avatar_url = upload_response.json()["avatar_url"]
        session_cookie = client.cookies.get(settings.session_cookie_name)

    restarted_app = create_app(settings=settings)
    with TestClient(restarted_app) as restarted_client:
        restarted_client.cookies.set(settings.session_cookie_name, session_cookie)
        session_response = restarted_client.get("/api/session")
        assert session_response.status_code == 200
        assert session_response.json()["user"]["avatar_url"] == avatar_url


def test_profile_update_changes_username_and_password(tmp_path) -> None:
    app = create_app(settings=build_settings(tmp_path))

    with TestClient(app) as client:
        register(client, "profile_dev", "Profile User")
        update_response = client.patch(
            "/api/users/me",
            json={
                "username": "profile_next",
                "display_name": "Profile Next",
                "current_password": "Supersecure123",
                "new_password": "EvenStronger123",
            },
        )
        assert update_response.status_code == 200
        assert update_response.json()["username"] == "profile_next"

        client.post("/api/auth/logout")
        login_response = client.post(
            "/api/auth/login",
            json={"username": "profile_next", "password": "EvenStronger123"},
        )
        assert login_response.status_code == 200

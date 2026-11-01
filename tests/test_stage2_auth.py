"""Тесты этапа 2: аутентификация (JWT + argon2), страницы, cookies, CSRF."""

from __future__ import annotations

from httpx import AsyncClient

REGISTER = {
    "username": "tester",
    "password": "Passw0rd1",
    "biometric_consent": True,
}


async def test_register_sets_cookies_and_returns_tokens(client: AsyncClient) -> None:
    resp = await client.post("/api/auth/register", json=REGISTER)
    assert resp.status_code == 201
    body = resp.json()
    assert body["access_token"] and body["refresh_token"]
    assert "fm_access" in resp.cookies
    assert "fm_refresh" in resp.cookies
    assert "fm_csrf" in resp.cookies


async def test_register_requires_biometric_consent(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"username": "noconsent", "password": "Passw0rd1", "biometric_consent": False},
    )
    assert resp.status_code == 422


async def test_register_duplicate_username(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=REGISTER)
    resp = await client.post("/api/auth/register", json=REGISTER)
    assert resp.status_code == 409


async def test_register_weak_password_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"username": "weakuser", "password": "12345678", "biometric_consent": True},
    )
    assert resp.status_code == 422


async def test_login_and_me(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=REGISTER)
    await client.post("/api/auth/logout")
    resp = await client.post(
        "/api/auth/login", json={"username": "tester", "password": "Passw0rd1"}
    )
    assert resp.status_code == 200
    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "tester"


async def test_login_wrong_password(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=REGISTER)
    await client.post("/api/auth/logout")
    resp = await client.post(
        "/api/auth/login", json={"username": "tester", "password": "wrongpass1"}
    )
    assert resp.status_code == 401


async def test_me_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_refresh_rotates_token(client: AsyncClient) -> None:
    resp = await client.post("/api/auth/register", json=REGISTER)
    old_refresh = resp.cookies["fm_refresh"]
    rotated = await client.post("/api/auth/refresh")
    assert rotated.status_code == 200
    assert rotated.cookies["fm_refresh"] != old_refresh


async def test_logout_clears_session(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=REGISTER)
    await client.post("/api/auth/logout")
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401


async def test_password_reset_flow(client: AsyncClient, db_session) -> None:
    from app.core import auth_service

    await client.post(
        "/api/auth/register",
        json={**REGISTER, "email": "reset@example.com"},
    )
    raw = await auth_service.create_password_reset(db_session, "reset@example.com")
    assert raw

    resp = await client.post(
        "/api/auth/password-reset/confirm",
        json={"token": raw, "new_password": "NewPassw0rd"},
    )
    assert resp.status_code == 200

    await client.post("/api/auth/logout")
    login = await client.post(
        "/api/auth/login", json={"username": "tester", "password": "NewPassw0rd"}
    )
    assert login.status_code == 200


async def test_password_reset_unknown_email_is_opaque(client: AsyncClient) -> None:
    resp = await client.post("/api/auth/password-reset", json={"email": "nobody@example.com"})
    assert resp.status_code == 200
    assert "отправлено" in resp.json()["detail"]


async def test_delete_account_removes_user(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=REGISTER)
    resp = await client.delete("/api/auth/account")
    assert resp.status_code == 200
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_landing_and_auth_pages_render(client: AsyncClient) -> None:
    for path, needle in (
        ("/", "Измерьте лицо"),
        ("/login", "Вход"),
        ("/register", "Регистрация"),
        ("/reset-password", "Восстановление пароля"),
    ):
        resp = await client.get(path)
        assert resp.status_code == 200
        assert needle in resp.text


async def test_authenticated_user_redirected_from_login(client: AsyncClient) -> None:
    await client.post("/api/auth/register", json=REGISTER)
    resp = await client.get("/login", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/profile"


async def test_csrf_token_header_present(client: AsyncClient) -> None:
    resp = await client.post("/api/auth/register", json=REGISTER)
    assert "x-csrf-token" in resp.headers

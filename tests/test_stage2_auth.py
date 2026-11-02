"""Тесты этапа 2: аутентификация (JWT + argon2), страницы, cookies, CSRF."""

from __future__ import annotations

import asyncio
from email import policy
from email.parser import BytesParser

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


# --- Отправка письма сброса пароля --------------------------------------------


class _SmtpSink:
    """Копит письма, принятые тестовым SMTP-сервером."""

    def __init__(self) -> None:
        self.messages: list = []


async def _start_smtp_server() -> tuple:
    """Поднимает настоящий SMTP-сервер на свободном порту localhost."""
    import socket

    from aiosmtpd.controller import Controller

    sink = _SmtpSink()

    class _Handler:
        async def handle_DATA(self, server, session, envelope):
            sink.messages.append(envelope)
            return "250 OK"

    # aiosmtpd не умеет port=0 (не узнаёт фактический порт), поэтому
    # подбираем свободный порт вручную и сразу его освобождаем.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    controller = Controller(_Handler(), hostname="127.0.0.1", port=port)
    controller.start()
    return controller, sink


async def test_password_reset_email_is_sent(client: AsyncClient, db_session, monkeypatch) -> None:
    """Письмо реально уходит на SMTP-сервер, а не просто пишется в лог.

    Проверяем без моков: поднимаем настоящий SMTP-сервер на localhost и
    убеждаемся, что письмо дошло и содержит ссылку с токеном.
    """
    from app.config import settings
    from app.core import auth_service

    controller, sink = await _start_smtp_server()
    try:
        monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
        monkeypatch.setattr(settings, "smtp_port", controller.port)
        monkeypatch.setattr(settings, "smtp_starttls", False)
        monkeypatch.setattr(settings, "smtp_user", "")
        monkeypatch.setattr(settings, "smtp_password", "")
        monkeypatch.setattr(settings, "mail_from", "no-reply@facemetric.test")

        await client.post("/api/auth/register", json={**REGISTER, "email": "mail@example.com"})
        raw = await auth_service.create_password_reset(db_session, "mail@example.com")
        assert raw

        # SMTP-обмен асинхронный: даём серверу принять письмо.
        for _ in range(50):
            if sink.messages:
                break
            await asyncio.sleep(0.05)

        assert len(sink.messages) == 1, "письмо не дошло до SMTP-сервера"
        envelope = sink.messages[0]
        assert envelope.rcpt_tos == ["mail@example.com"]

        # Кириллица уходит в base64 внутри multipart, поэтому разбираем
        # письмо штатным парсером, а не поиском подстроки в сырых байтах.
        message = BytesParser(policy=policy.default).parsebytes(envelope.content)
        assert "FaceMetric" in message["Subject"]
        text_part = message.get_body(preferencelist=("plain",)).get_content()
        assert "reset-password?token=" in text_part
        assert raw in text_part
    finally:
        controller.stop()


async def test_password_reset_falls_back_to_log_without_smtp(
    client: AsyncClient, db_session, monkeypatch
) -> None:
    """Без SMTP токен всё равно создаётся, а ссылка попадает в лог."""
    from app.config import settings
    from app.core import auth_service

    monkeypatch.setattr(settings, "smtp_host", "")

    await client.post("/api/auth/register", json={**REGISTER, "email": "log@example.com"})
    raw = await auth_service.create_password_reset(db_session, "log@example.com")
    assert raw

    resp = await client.post(
        "/api/auth/password-reset/confirm",
        json={"token": raw, "new_password": "NewPassw0rd"},
    )
    assert resp.status_code == 200


async def test_mail_send_failure_does_not_break_reset(
    client: AsyncClient, db_session, monkeypatch
) -> None:
    """Недоступный SMTP не должен ломать запрос сброса пароля."""
    from app.config import settings
    from app.core import auth_service

    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "smtp_port", 1)  # порт, куда никто не слушает
    monkeypatch.setattr(settings, "smtp_timeout_seconds", 2.0)

    await client.post("/api/auth/register", json={**REGISTER, "email": "fail@example.com"})
    raw = await auth_service.create_password_reset(db_session, "fail@example.com")
    assert raw, "токен должен создаваться даже при сбое отправки"


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


# --- CSRF: защита от межсайтовых мутирующих запросов -------------------------


async def test_foreign_origin_blocks_mutation(client: AsyncClient) -> None:
    """POST с чужого origin отклоняется, даже если cookie валидна."""
    await client.post("/api/auth/register", json=REGISTER)
    resp = await client.post("/api/photos/1/preview", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert "origin" in resp.json()["detail"].lower()


async def test_foreign_referer_does_not_block(client: AsyncClient) -> None:
    """Чужой Referer без Origin не блокируется: Referer — данные, не признак CSRF.

    Аналитический маячок штатно получает referer внешнего сайта; блокировка по
    нему сломала бы учёт переходов.
    """
    await client.post("/api/auth/register", json=REGISTER)
    resp = await client.post("/api/auth/logout", headers={"Referer": "https://evil.example/x"})
    assert resp.status_code == 200


async def test_same_origin_mutation_allowed(client: AsyncClient) -> None:
    """Свой origin (base_url) пропускается."""
    await client.post("/api/auth/register", json=REGISTER)
    resp = await client.post("/api/auth/logout", headers={"Origin": "http://test"})
    assert resp.status_code == 200


async def test_no_origin_client_allowed(client: AsyncClient) -> None:
    """Запрос без Origin/Referer (скрипты, API-клиенты) не блокируется.

    CSRF — атака через браузер; у серверного клиента cookies нет, и
    требовать заголовок значило бы сломать все не-браузерные интеграции.
    """
    await client.post("/api/auth/register", json=REGISTER)
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 200


async def test_safe_method_ignores_foreign_origin(client: AsyncClient) -> None:
    """GET с чужого origin не блокируется: состояние не меняется."""
    resp = await client.get("/health", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 200

"""Отправка писем через SMTP.

Почта нужна для восстановления пароля. Если SMTP не настроен (нет
`SMTP_HOST`), письмо не отправляется, а ссылка уходит в лог — приложение
остаётся рабочим на локальной машине и в тестах.

`aiosmtplib` импортируется лениво: он лежит в extra `mail`, и без него
сервис поднимается как обычно, просто письма не уходят.
"""

from __future__ import annotations

from email.message import EmailMessage

import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


def smtp_configured() -> bool:
    """Настроен ли SMTP. Без хоста отправка невозможна."""
    return bool(settings.smtp_host)


async def send_email(to: str, subject: str, text: str, html: str | None = None) -> bool:
    """Отправляет письмо. Возвращает False, если почта не ушла.

    Ошибки не пробрасываются: сбой почтового сервера не должен ломать
    запрос на сброс пароля. Причина пишется в лог.
    """
    if not smtp_configured():
        logger.info("mail.skipped", reason="smtp_not_configured", to=to)
        return False

    try:
        import aiosmtplib
    except ImportError:
        logger.warning("mail.skipped", reason="aiosmtplib_missing", to=to)
        return False

    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            start_tls=settings.smtp_starttls,
            timeout=settings.smtp_timeout_seconds,
        )
    except Exception as exc:
        # Пароль и текст письма не логируем: в теле лежит токен сброса.
        logger.error("mail.send_failed", to=to, error=str(exc))
        return False

    logger.info("mail.sent", to=to)
    return True


async def send_password_reset(to: str, reset_url: str) -> bool:
    """Письмо со ссылкой сброса пароля."""
    text = (
        "Вы запросили сброс пароля в FaceMetric.\n\n"
        f"Перейдите по ссылке, чтобы задать новый пароль:\n{reset_url}\n\n"
        "Ссылка действует 2 часа. Если вы не запрашивали сброс, просто "
        "проигнорируйте это письмо — пароль останется прежним."
    )
    html = (
        "<p>Вы запросили сброс пароля в FaceMetric.</p>"
        f'<p><a href="{reset_url}">Задать новый пароль</a></p>'
        "<p>Ссылка действует 2 часа. Если вы не запрашивали сброс, просто "
        "проигнорируйте это письмо — пароль останется прежним.</p>"
    )
    return await send_email(to, "FaceMetric: сброс пароля", text, html)

"""Определение IP клиента с учётом обратного прокси.

За платформенным прокси (Render и т.п.) request.client.host — это адрес
самого прокси, а не пользователя. Если ключевать лимиты по нему, все
посетители попадают в одну корзину: один активный клиент исчерпывает общий
лимит и блокирует остальных.

X-Forwarded-For — это список «кто шёл до нас», где прокси дописывает адрес
источника в конец. Поэтому при одном доверенном прокси верный адрес — самый
правый элемент: клиент не может подделать его, добавив свой заголовок (его
значение окажется левее). Читаем заголовок только когда прокси явно доверенный
(settings.trust_proxy_headers): иначе клиент подделал бы XFF и обошёл лимиты.
"""

from __future__ import annotations

from fastapi import Request
from slowapi.util import get_remote_address

from app.config import settings


def client_ip(request: Request) -> str:
    """IP клиента: из X-Forwarded-For за доверенным прокси, иначе — соединение."""
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            hops = [part.strip() for part in forwarded.split(",") if part.strip()]
            if hops:
                return hops[-1]
    return get_remote_address(request)

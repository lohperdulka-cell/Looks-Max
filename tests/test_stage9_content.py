"""Тесты этапа 9: контент, SEO, аналитика, обратная связь, markdown."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.models.content import Article, FaqItem
from app.models.user import User
from app.services import analytics as analytics_service
from app.services import markdown_lite
from httpx import AsyncClient
from sqlalchemy import select


async def _admin(client: AsyncClient, db_session) -> User:
    await client.post(
        "/api/auth/register",
        json={"username": "boss", "password": "Passw0rd1", "biometric_consent": True},
    )
    user = (await db_session.execute(select(User).where(User.username == "boss"))).scalar_one()
    user.is_admin = True
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _seed_article(db_session, **kwargs) -> Article:
    defaults = {
        "slug": "kak-chitat-otchet",
        "title": "Как читать отчёт",
        "summary": "Разбор метрик без паники",
        "body_md": "# Заголовок\n\nТекст **важный**.",
        "is_published": True,
        "views": 0,
        "published_at": datetime.now(UTC),
    }
    defaults.update(kwargs)
    article = Article(**defaults)
    db_session.add(article)
    await db_session.commit()
    await db_session.refresh(article)
    return article


# --- markdown ----------------------------------------------------------------


def test_markdown_escapes_html() -> None:
    html = markdown_lite.render("<script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_markdown_headings_lists_and_emphasis() -> None:
    html = markdown_lite.render("# H1\n\n**b** *i*\n\n- a\n- b\n\n1. x\n\n> q")
    assert "<h1>H1</h1>" in html
    assert "<strong>b</strong>" in html
    assert "<em>i</em>" in html
    assert "<ul><li>a</li><li>b</li></ul>" in html
    assert "<ol><li>x</li></ol>" in html
    assert "<blockquote>q</blockquote>" in html


def test_markdown_links_get_nofollow() -> None:
    html = markdown_lite.render("[t](https://example.com)")
    assert 'rel="noopener noreferrer nofollow"' in html
    assert 'href="https://example.com"' in html


def test_markdown_javascript_link_is_not_rendered() -> None:
    html = markdown_lite.render("[t](javascript:alert(1))")
    assert "href=" not in html


def test_markdown_link_url_cannot_break_out_of_href() -> None:
    """Кавычка в URL не должна закрывать атрибут и добавлять обработчик.

    URL идёт в атрибут href, поэтому его мало экранировать как текст
    (quote=False не трогает кавычки) — иначе получается внедрение атрибута.
    """
    html = markdown_lite.render('[click](https://x.com/"onmouseover="alert(1))')
    assert '"onmouseover=' not in html
    assert "&quot;onmouseover=&quot;" in html


def test_markdown_plain_text_strips_markup() -> None:
    assert markdown_lite.plain_text("# H\n**b** [t](https://e.com)", 100) == "H b t"


def test_markdown_empty_input() -> None:
    assert markdown_lite.render(None) == ""
    assert markdown_lite.plain_text(None) == ""


# --- статьи ------------------------------------------------------------------


async def test_articles_list_empty(client: AsyncClient) -> None:
    body = (await client.get("/api/articles")).json()
    assert body == {"items": [], "total": 0, "limit": 20, "offset": 0}


async def test_articles_list_hides_drafts(client: AsyncClient, db_session) -> None:
    await _seed_article(db_session)
    await _seed_article(db_session, slug="draft", title="Черновик", is_published=False)
    body = (await client.get("/api/articles")).json()
    assert body["total"] == 1
    assert body["items"][0]["slug"] == "kak-chitat-otchet"


async def test_article_detail_increments_views(client: AsyncClient, db_session) -> None:
    await _seed_article(db_session)
    first = await client.get("/api/articles/kak-chitat-otchet")
    assert first.status_code == 200
    assert first.json()["views"] == 1
    second = await client.get("/api/articles/kak-chitat-otchet")
    assert second.json()["views"] == 2


async def test_article_draft_returns_404(client: AsyncClient, db_session) -> None:
    await _seed_article(db_session, slug="draft", is_published=False)
    assert (await client.get("/api/articles/draft")).status_code == 404


async def test_unknown_article_returns_404(client: AsyncClient) -> None:
    assert (await client.get("/api/articles/nope")).status_code == 404


# --- FAQ и метрики -----------------------------------------------------------


async def test_faq_api_returns_only_active_sorted(client: AsyncClient, db_session) -> None:
    db_session.add_all(
        [
            FaqItem(question="B?", answer_md="b", category="x", sort_order=2, is_active=True),
            FaqItem(question="A?", answer_md="a", category="x", sort_order=1, is_active=True),
            FaqItem(question="C?", answer_md="c", category="x", sort_order=0, is_active=False),
        ]
    )
    await db_session.commit()
    rows = (await client.get("/api/faq")).json()
    assert [r["question"] for r in rows] == ["A?", "B?"]


async def test_metrics_reference_api_matches_constants(client: AsyncClient) -> None:
    from app.ml.constants import METRIC_SPECS

    rows = (await client.get("/api/metrics/reference")).json()
    assert len(rows) == len(METRIC_SPECS)
    assert {r["key"] for r in rows} == {s.key for s in METRIC_SPECS}
    sample = next(r for r in rows if r["key"] == METRIC_SPECS[0].key)
    assert sample["reference"] == METRIC_SPECS[0].reference
    assert sample["weight"] == METRIC_SPECS[0].weight
    assert sample["source"]


async def test_metrics_page_renders_all_metrics(client: AsyncClient) -> None:
    from app.ml.constants import METRIC_SPECS

    resp = await client.get("/metrics")
    assert resp.status_code == 200
    for spec in METRIC_SPECS:
        assert spec.label in resp.text


# --- страницы ----------------------------------------------------------------


async def test_blog_page_renders_articles(client: AsyncClient, db_session) -> None:
    await _seed_article(db_session)
    resp = await client.get("/blog")
    assert resp.status_code == 200
    assert "Как читать отчёт" in resp.text
    assert 'href="/blog/kak-chitat-otchet"' in resp.text


async def test_blog_page_empty_state(client: AsyncClient) -> None:
    resp = await client.get("/blog")
    assert resp.status_code == 200
    assert "Статей пока нет" in resp.text


async def test_article_page_renders_markdown_and_json_ld(client: AsyncClient, db_session) -> None:
    await _seed_article(db_session)
    resp = await client.get("/blog/kak-chitat-otchet")
    assert resp.status_code == 200
    assert "<strong>важный</strong>" in resp.text
    assert "application/ld+json" in resp.text
    assert '"@type": "Article"' in resp.text


async def test_article_json_ld_escapes_script_close(client: AsyncClient, db_session) -> None:
    """Заголовок статьи не должен закрывать тег ld+json и внедрять скрипт.

    json.dumps + `| safe` не экранирует «</script>», поэтому заголовок вида
    `</script><script>...` исполнялся бы как разметка. Фильтр tojson закрывает
    «<»/«>», оставляя валидный JSON.
    """
    await _seed_article(
        db_session,
        slug="evil",
        title="</script><script>alert(1)</script>",
    )
    resp = await client.get("/blog/evil")
    assert resp.status_code == 200
    assert "</script><script>alert(1)</script>" not in resp.text
    assert "\\u003c/script\\u003e" in resp.text


async def test_article_page_404_for_draft(client: AsyncClient, db_session) -> None:
    await _seed_article(db_session, slug="draft", is_published=False)
    resp = await client.get("/blog/draft")
    assert resp.status_code == 404
    assert "404" in resp.text


async def test_static_content_pages_render(client: AsyncClient) -> None:
    for path, marker in [
        ("/about", "Принципы"),
        ("/contacts", "Написать нам"),
        ("/help", "Как получить честную оценку"),
        ("/privacy", "Какие данные мы обрабатываем"),
        ("/terms", "Обязанности пользователя"),
    ]:
        resp = await client.get(path)
        assert resp.status_code == 200, path
        assert marker in resp.text, path


async def test_help_page_uses_faq_from_db(client: AsyncClient, db_session) -> None:
    db_session.add(
        FaqItem(question="Уникальный вопрос?", answer_md="Ответ.", category="x", sort_order=0)
    )
    await db_session.commit()
    resp = await client.get("/help")
    assert "Уникальный вопрос?" in resp.text


async def test_analytics_beacon_included_on_public_pages(client: AsyncClient) -> None:
    resp = await client.get("/")
    assert "/static/js/analytics.js" in resp.text


# --- обратная связь ----------------------------------------------------------


async def test_feedback_creates_record(client: AsyncClient, db_session) -> None:
    from app.models.content import Feedback

    resp = await client.post(
        "/api/feedback",
        json={"subject": "Баг", "message": "Не грузится HEIC", "email": "u@e.com"},
    )
    assert resp.status_code == 201
    row = (await db_session.execute(select(Feedback))).scalar_one()
    assert row.subject == "Баг"
    assert row.email == "u@e.com"
    assert row.is_processed is False


async def test_feedback_validation_rejects_short_message(client: AsyncClient) -> None:
    resp = await client.post("/api/feedback", json={"subject": "Тема", "message": "ok"})
    assert resp.status_code == 422


async def test_feedback_validation_rejects_short_subject(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/feedback", json={"subject": "a", "message": "достаточно длинный"}
    )
    assert resp.status_code == 422


# --- аналитика ---------------------------------------------------------------


async def test_ping_records_view(client: AsyncClient, db_session) -> None:
    from app.models.content import PageView

    resp = await client.post(
        "/api/analytics/ping",
        json={"path": "/blog/post?utm=1", "referer": "https://google.com/search"},
    )
    assert resp.status_code == 204
    row = (await db_session.execute(select(PageView))).scalar_one()
    assert row.path == "/blog/post"
    assert row.kind == "article"
    assert row.referer_host == "google.com"


async def test_ping_uses_header_referer_when_body_empty(client: AsyncClient, db_session) -> None:
    from app.models.content import PageView

    await client.post(
        "/api/analytics/ping",
        json={"path": "/"},
        headers={"referer": "https://twitter.com/x"},
    )
    row = (await db_session.execute(select(PageView))).scalar_one()
    assert row.kind == "landing"
    assert row.referer_host == "twitter.com"


async def test_analytics_summary_requires_admin(client: AsyncClient) -> None:
    assert (await client.get("/api/admin/analytics")).status_code == 401


async def test_analytics_summary_aggregates(client: AsyncClient, db_session) -> None:
    await _admin(client, db_session)
    for path in ["/", "/blog/a", "/blog/b", "/blog/a"]:
        await client.post("/api/analytics/ping", json={"path": path})

    body = (await client.get("/api/admin/analytics?days=30")).json()
    assert body["total_views"] == 4
    kinds = {row["kind"]: row["views"] for row in body["by_kind"]}
    assert kinds == {"article": 3, "landing": 1}
    assert body["top_pages"][0]["path"] == "/blog/a"
    assert body["top_pages"][0]["views"] == 2


async def test_classify_and_normalize_helpers() -> None:
    assert analytics_service.classify("/") == "landing"
    assert analytics_service.classify("/blog/x") == "article"
    assert analytics_service.classify("/r/token") == "public_report"
    assert analytics_service.classify("/about") == "page"
    assert analytics_service.normalize_path("/a/b?x=1#z") == "/a/b"
    assert analytics_service.normalize_path("") == "/"
    assert analytics_service.referer_host(None) is None
    assert analytics_service.referer_host("https://sub.example.com/p") == "sub.example.com"


async def test_views_outside_window_excluded(client: AsyncClient, db_session) -> None:
    from datetime import timedelta

    from app.models.content import PageView

    old = datetime.now(UTC) - timedelta(days=40)
    db_session.add(PageView(path="/old", kind="page", created_at=old))
    await db_session.commit()
    await _admin(client, db_session)
    body = (await client.get("/api/admin/analytics?days=7")).json()
    assert body["total_views"] == 0


# --- SEO ---------------------------------------------------------------------


async def test_sitemap_includes_static_pages_and_articles(client: AsyncClient, db_session) -> None:
    await _seed_article(db_session)
    resp = await client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    assert "<urlset" in resp.text
    assert "/blog/kak-chitat-otchet</loc>" in resp.text
    assert "/metrics</loc>" in resp.text


async def test_sitemap_excludes_drafts(client: AsyncClient, db_session) -> None:
    await _seed_article(db_session, slug="draft", is_published=False)
    resp = await client.get("/sitemap.xml")
    assert "draft" not in resp.text


async def test_robots_disallows_service_sections(client: AsyncClient) -> None:
    resp = await client.get("/robots.txt")
    assert resp.status_code == 200
    assert "Disallow: /api/" in resp.text
    assert "Disallow: /admin" in resp.text
    assert "Sitemap:" in resp.text


@pytest.mark.parametrize("path", ["/", "/metrics", "/blog", "/about", "/contacts", "/help"])
async def test_public_pages_have_description_meta(client: AsyncClient, path: str) -> None:
    resp = await client.get(path)
    assert 'name="description"' in resp.text


# --- фронтенд: защита от XSS при подстановке данных из API --------------------


def test_js_sinks_escape_user_controlled_text() -> None:
    """Данные из API подставляются в innerHTML — они должны быть экранированы.

    display_name, owner и текст ошибок контролирует пользователь, поэтому
    интерполяция их в разметку без escapeHtml — это XSS.
    """
    from pathlib import Path

    js_dir = Path(__file__).resolve().parents[1] / "app" / "static" / "js"
    battles = (js_dir / "battles.js").read_text(encoding="utf-8")
    app = (js_dir / "app.js").read_text(encoding="utf-8")

    assert "escapeHtml(photo.owner)" in battles
    assert "escapeHtml(row.display_name" in battles
    assert "escapeHtml(err.message)" in battles
    assert "escapeHtml(user.display_name" in app


def test_toast_renders_message_as_text_not_markup() -> None:
    """Тост получает detail из ответов API, поэтому не должен использовать innerHTML."""
    import re
    from pathlib import Path

    app = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    toast_body = app.split("const toast = ")[1].split("window.fmToast")[0]
    # Комментарии упоминают innerHTML как антипример — сравниваем только код.
    code = re.sub(r"//[^\n]*", "", toast_body)
    assert "innerHTML" not in code
    assert "createTextNode" in code

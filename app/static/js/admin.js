/* ==========================================================================
   FaceMetric — админка: дашборд, пользователи, модерация, веса, контент, логи.
   ========================================================================== */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const api = window.fmApi;
  const toast = window.fmToast;
  const escapeHtml = window.fmEscape;
  if (!api || !$('[data-admin-tabs]')) return;

  let chart = null;
  let loaded = {};

  const KIND_LABELS = {
    landing: 'лендинг',
    article: 'статьи',
    public_report: 'публичные отчёты',
    page: 'прочие страницы',
  };

  const statCard = (label, value, accent) => `
    <div class="admin-stat card ${accent ? 'is-accent' : ''}">
      <span>${value}</span><small>${label}</small>
    </div>`;

  const loadDashboard = async () => {
    const [stats, activity] = await Promise.all([
      api('/api/admin/dashboard'),
      api('/api/admin/activity?days=14'),
    ]);
    $('[data-admin-stats]').innerHTML = [
      statCard('пользователей', stats.users_total, true),
      statCard('новых за 7 дней', stats.users_new_7d),
      statCard('забанено', stats.users_banned),
      statCard('фото', stats.photos_total),
      statCard('отчётов', stats.reports_total),
      statCard('отчётов за 7 дней', stats.reports_7d),
      statCard('батлов', stats.battles_total),
      statCard('голосов', stats.votes_total),
      statCard('упражнений', stats.exercises_done),
      statCard('непрочитанных обращений', stats.feedback_unread),
      statCard('ошибок за сутки', stats.errors_24h),
    ].join('');

    if (typeof Chart === 'undefined') return;
    const canvas = $('[data-admin-chart]');
    const labels = activity.map((p) => p.day.slice(5));
    const style = getComputedStyle(document.documentElement);
    const accent = style.getPropertyValue('--accent').trim() || '#4da3ff';
    const ok = style.getPropertyValue('--ok').trim() || '#3ddc97';

    if (chart) chart.destroy();
    chart = new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Регистрации', data: activity.map((p) => p.users), borderColor: accent, tension: .3 },
          { label: 'Отчёты', data: activity.map((p) => p.reports), borderColor: ok, tension: .3 },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: style.getPropertyValue('--fg').trim() } } },
        scales: {
          x: { ticks: { color: style.getPropertyValue('--fg-muted').trim() } },
          y: { beginAtZero: true, ticks: { color: style.getPropertyValue('--fg-muted').trim() } },
        },
      },
    });
  };

  const userRow = (u) => `
    <tr>
      <td>${u.id}</td>
      <td>${escapeHtml(u.username)}</td>
      <td class="muted small">${escapeHtml(u.email || '—')}</td>
      <td>${u.elo}</td>
      <td>${u.is_admin ? 'админ' : 'юзер'}</td>
      <td>${u.is_banned ? '<span class="tag tag-danger">забанен</span>' : (u.is_active ? '<span class="tag tag-ok">активен</span>' : '<span class="tag">выключен</span>')}</td>
      <td class="admin-actions">
        <button class="btn btn-ghost btn-sm" data-user-ban="${u.id}" data-value="${u.is_banned ? 'false' : 'true'}">
          ${u.is_banned ? 'Разбанить' : 'Забанить'}
        </button>
        <button class="btn btn-ghost btn-sm" data-user-admin="${u.id}" data-value="${u.is_admin ? 'false' : 'true'}">
          ${u.is_admin ? 'Снять админа' : 'Сделать админом'}
        </button>
      </td>
    </tr>`;

  const loadUsers = async (query = '') => {
    const suffix = query ? `?query=${encodeURIComponent(query)}` : '';
    const data = await api(`/api/admin/users${suffix}`);
    $('[data-users-body]').innerHTML = data.items.length
      ? data.items.map(userRow).join('')
      : '<tr><td colspan="7" class="muted">Пользователи не найдены.</td></tr>';

    $$('[data-user-ban]').forEach((btn) =>
      btn.addEventListener('click', () => patchUser(Number(btn.dataset.userBan), { is_banned: btn.dataset.value === 'true' })));
    $$('[data-user-admin]').forEach((btn) =>
      btn.addEventListener('click', () => patchUser(Number(btn.dataset.userAdmin), { is_admin: btn.dataset.value === 'true' })));
  };

  const patchUser = async (id, body) => {
    try {
      await api(`/api/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
      toast('Сохранено', 'success');
      await loadUsers($('[data-users-search] input').value);
    } catch (err) {
      toast(err.message || 'Не удалось изменить пользователя', 'error');
    }
  };

  const loadPhotos = async () => {
    const onlyPublic = $('[data-photos-public-only]').checked;
    const data = await api(`/api/admin/photos?only_public=${onlyPublic}`);
    $('[data-photos-grid]').innerHTML = data.items.length
      ? data.items.map((p) => `
        <figure class="mod-item ${p.is_hidden ? 'is-hidden' : ''}">
          <img src="${p.image_url}" alt="Фото ${p.id}" loading="lazy">
          <figcaption>
            <span class="muted small">@${escapeHtml(p.username)} · #${p.id}</span>
            <span class="muted small">Q ${p.quality_score != null ? p.quality_score.toFixed(2) : '—'}</span>
            <button class="btn btn-ghost btn-sm" data-photo-hide="${p.id}" data-value="${p.is_hidden ? 'false' : 'true'}">
              ${p.is_hidden ? 'Вернуть' : 'Скрыть'}
            </button>
          </figcaption>
        </figure>`).join('')
      : '<p class="muted">Фото нет.</p>';

    $$('[data-photo-hide]').forEach((btn) =>
      btn.addEventListener('click', () => togglePhoto(Number(btn.dataset.photoHide), btn.dataset.value === 'true')));
  };

  const togglePhoto = async (id, hidden) => {
    try {
      await api(`/api/admin/photos/${id}/hide?hidden=${hidden}`, { method: 'POST' });
      toast(hidden ? 'Фото скрыто' : 'Фото возвращено', 'success');
      await loadPhotos();
    } catch (err) {
      toast(err.message || 'Не удалось изменить фото', 'error');
    }
  };

  const loadWeights = async () => {
    const rows = await api('/api/admin/metric-weights');
    $('[data-weights-body]').innerHTML = rows.length
      ? rows.map((w) => `
        <tr>
          <td>${escapeHtml(w.key)}</td>
          <td>${w.weight.toFixed(2)}</td>
          <td>${w.reference_value != null ? w.reference_value : '—'}</td>
          <td>${w.is_enabled ? 'да' : 'нет'}</td>
          <td class="muted small">${escapeHtml(w.note || '')}</td>
          <td><button class="btn btn-ghost btn-sm" data-weight-del="${escapeHtml(w.key)}">Сбросить</button></td>
        </tr>`).join('')
      : '<tr><td colspan="6" class="muted">Переопределений нет — используются значения из кода.</td></tr>';

    $$('[data-weight-del]').forEach((btn) =>
      btn.addEventListener('click', async () => {
        try {
          await api(`/api/admin/metric-weights/${encodeURIComponent(btn.dataset.weightDel)}`, { method: 'DELETE' });
          toast('Переопределение снято', 'success');
          await loadWeights();
        } catch (err) {
          toast(err.message || 'Не удалось сбросить', 'error');
        }
      }));
  };

  const loadFeedback = async () => {
    const onlyUnread = $('[data-feedback-unread]').checked;
    const rows = await api(`/api/admin/feedback?only_unread=${onlyUnread}`);
    $('[data-feedback-list]').innerHTML = rows.length
      ? rows.map((f) => `
        <article class="feedback-card card ${f.is_processed ? '' : 'is-new'}">
          <header>
            <strong>${escapeHtml(f.subject)}</strong>
            <span class="muted small">${escapeHtml(f.email || f.name || 'анонимно')} · ${new Date(f.created_at).toLocaleString('ru-RU')}</span>
          </header>
          <p>${escapeHtml(f.message)}</p>
          ${f.is_processed
            ? '<span class="tag tag-ok">обработано</span>'
            : `<button class="btn btn-primary btn-sm" data-feedback-process="${f.id}">Отметить обработанным</button>`}
        </article>`).join('')
      : '<p class="muted">Обращений нет.</p>';

    $$('[data-feedback-process]').forEach((btn) =>
      btn.addEventListener('click', async () => {
        try {
          await api(`/api/admin/feedback/${btn.dataset.feedbackProcess}/process`, { method: 'POST' });
          toast('Обращение обработано', 'success');
          await loadFeedback();
        } catch (err) {
          toast(err.message || 'Не удалось обновить', 'error');
        }
      }));
  };

  const loadContent = async () => {
    const [articles, faq] = await Promise.all([api('/api/admin/articles'), api('/api/admin/faq')]);
    $('[data-articles-body]').innerHTML = articles.length
      ? articles.map((a) => `
        <tr>
          <td>${escapeHtml(a.title)}</td>
          <td class="muted small">${escapeHtml(a.slug)}</td>
          <td>${a.is_published ? '<span class="tag tag-ok">опубликована</span>' : '<span class="tag">черновик</span>'}</td>
          <td>${a.views}</td>
          <td><button class="btn btn-ghost btn-sm" data-article-del="${a.id}">Удалить</button></td>
        </tr>`).join('')
      : '<tr><td colspan="5" class="muted">Статей нет.</td></tr>';

    $('[data-faq-body]').innerHTML = faq.length
      ? faq.map((f) => `
        <tr>
          <td>${escapeHtml(f.question)}</td>
          <td class="muted small">${escapeHtml(f.category)}</td>
          <td>${f.sort_order}</td>
          <td>${f.is_active ? 'да' : 'нет'}</td>
          <td><button class="btn btn-ghost btn-sm" data-faq-del="${f.id}">Удалить</button></td>
        </tr>`).join('')
      : '<tr><td colspan="5" class="muted">Вопросов нет.</td></tr>';

    $$('[data-article-del]').forEach((btn) =>
      btn.addEventListener('click', async () => {
        await api(`/api/admin/articles/${btn.dataset.articleDel}`, { method: 'DELETE' });
        toast('Статья удалена', 'success');
        await loadContent();
      }));
    $$('[data-faq-del]').forEach((btn) =>
      btn.addEventListener('click', async () => {
        await api(`/api/admin/faq/${btn.dataset.faqDel}`, { method: 'DELETE' });
        toast('Вопрос удалён', 'success');
        await loadContent();
      }));
  };

  const loadAnalytics = async () => {
    const days = Number($('[data-analytics-days]').value || 30);
    const data = await api(`/api/admin/analytics?days=${days}`);
    $('[data-analytics-total]').textContent = `Просмотров за период: ${data.total_views}`;
    $('[data-analytics-stats]').innerHTML = data.by_kind.length
      ? data.by_kind.map((k) => statCard(KIND_LABELS[k.kind] || k.kind, k.views)).join('')
      : '<div class="muted small">Просмотров пока нет.</div>';

    $('[data-analytics-pages]').innerHTML = data.top_pages.length
      ? data.top_pages.map((p) => `
        <tr><td>${escapeHtml(p.path)}</td><td>${p.views}</td></tr>`).join('')
      : '<tr><td colspan="2" class="muted">Нет данных.</td></tr>';

    $('[data-analytics-referers]').innerHTML = data.top_referers.length
      ? data.top_referers.map((r) => `
        <tr><td>${escapeHtml(r.host)}</td><td>${r.views}</td></tr>`).join('')
      : '<tr><td colspan="2" class="muted">Внешних переходов нет.</td></tr>';
  };

  const loadLogs = async () => {
    const [logs, errors] = await Promise.all([api('/api/admin/logs'), api('/api/admin/errors')]);
    $('[data-logs-body]').innerHTML = logs.length
      ? logs.map((l) => `
        <tr>
          <td class="muted small">${new Date(l.created_at).toLocaleString('ru-RU')}</td>
          <td>${l.admin_id ?? '—'}</td>
          <td>${escapeHtml(l.action)}</td>
          <td class="muted small">${escapeHtml(l.target_type || '')} ${l.target_id ?? ''}</td>
        </tr>`).join('')
      : '<tr><td colspan="4" class="muted">Записей нет.</td></tr>';

    $('[data-errors-body]').innerHTML = errors.length
      ? errors.map((e) => `
        <tr>
          <td class="muted small">${new Date(e.created_at).toLocaleString('ru-RU')}</td>
          <td>${escapeHtml(e.level)}</td>
          <td class="muted small">${escapeHtml(e.path || '')}</td>
          <td class="muted small">${escapeHtml(e.message).slice(0, 160)}</td>
        </tr>`).join('')
      : '<tr><td colspan="4" class="muted">Ошибок нет.</td></tr>';
  };

  const loaders = {
    dashboard: loadDashboard,
    users: loadUsers,
    photos: loadPhotos,
    weights: loadWeights,
    feedback: loadFeedback,
    content: loadContent,
    analytics: loadAnalytics,
    logs: loadLogs,
  };

  const showTab = async (tab) => {
    $$('[data-panel]').forEach((panel) => (panel.hidden = panel.dataset.panel !== tab));
    $$('[data-tab]').forEach((btn) => btn.classList.toggle('is-active', btn.dataset.tab === tab));
    if (loaded[tab]) return;
    try {
      await loaders[tab]();
      loaded[tab] = true;
    } catch (err) {
      toast(err.message || 'Не удалось загрузить раздел', 'error');
    }
  };

  $$('[data-tab]').forEach((btn) =>
    btn.addEventListener('click', () => showTab(btn.dataset.tab)));

  $('[data-users-search]').addEventListener('submit', (event) => {
    event.preventDefault();
    loadUsers(event.target.query.value);
  });
  $('[data-photos-public-only]').addEventListener('change', loadPhotos);
  $('[data-feedback-unread]').addEventListener('change', loadFeedback);
  $('[data-analytics-days]').addEventListener('change', loadAnalytics);

  $('[data-weights-form]').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    const ref = form.reference_value.value;
    try {
      await api('/api/admin/metric-weights', {
        method: 'PUT',
        body: JSON.stringify({
          key: form.key.value.trim(),
          weight: Number(form.weight.value),
          reference_value: ref === '' ? null : Number(ref),
          note: form.note.value || null,
          is_enabled: true,
        }),
      });
      toast('Вес сохранён', 'success');
      form.reset();
      await loadWeights();
    } catch (err) {
      toast(err.message || 'Не удалось сохранить вес', 'error');
    }
  });

  $('[data-article-form]').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api('/api/admin/articles', {
        method: 'POST',
        body: JSON.stringify({
          title: form.title.value,
          summary: form.summary.value || null,
          body_md: form.body_md.value,
          is_published: form.is_published.checked,
        }),
      });
      toast('Статья добавлена', 'success');
      form.reset();
      await loadContent();
    } catch (err) {
      toast(err.message || 'Не удалось добавить статью', 'error');
    }
  });

  $('[data-faq-form]').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    try {
      await api('/api/admin/faq', {
        method: 'POST',
        body: JSON.stringify({
          question: form.question.value,
          answer_md: form.answer_md.value,
          category: form.category.value || 'general',
          sort_order: Number(form.sort_order.value) || 0,
          is_active: true,
        }),
      });
      toast('Вопрос добавлен', 'success');
      form.reset();
      await loadContent();
    } catch (err) {
      toast(err.message || 'Не удалось добавить вопрос', 'error');
    }
  });

  showTab('dashboard');
})();

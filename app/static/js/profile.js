/* ==========================================================================
   FaceMetric — профиль: статистика, график прогресса, до/после, достижения.
   ========================================================================== */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const api = window.fmApi;
  const toast = window.fmToast;
  if (!api || !$('[data-achievements]')) return;

  const progressChart = { instance: null };

  const scoreColor = (score) => (score >= 7.5 ? '#16a34a' : score >= 5 ? '#ca8a04' : '#dc2626');

  const renderProfile = (profile) => {
    $('[data-display-name]').textContent = profile.display_name || `@${profile.username}`;
    $('[data-profile-meta]').textContent =
      `@${profile.username} · на платформе с ${new Date(profile.created_at).toLocaleDateString('ru-RU')}`;
    $('[data-streak]').textContent = `🔥 серия: ${profile.streak_days}`;
    $('[data-elo]').textContent = `ELO: ${profile.elo}`;
    $('[data-score]').textContent = `Лучший балл: ${profile.stats.best_score.toFixed(1)}`;
    $('[data-avatar]').textContent = (profile.display_name || profile.username).slice(0, 1).toUpperCase();

    Object.entries(profile.stats).forEach(([key, value]) => {
      const el = $(`[data-stat="${key}"]`);
      if (!el) return;
      el.textContent = typeof value === 'number' && !Number.isInteger(value) ? value.toFixed(2) : value;
    });

    const form = $('[data-profile-form]');
    if (form) {
      form.display_name.value = profile.display_name || '';
      form.gender.value = profile.gender || '';
      form.birth_year.value = profile.birth_year || '';
      form.show_in_battles.checked = Boolean(profile.show_in_battles);
    }
  };

  const renderProgress = (data) => {
    const deltaEl = $('[data-progress-delta]');
    if (deltaEl) {
      const sign = data.delta > 0 ? '+' : '';
      deltaEl.textContent = data.delta
        ? `Изменение за период: ${sign}${data.delta.toFixed(2)} балла`
        : 'Недостаточно данных за период для оценки динамики.';
    }

    if (typeof Chart === 'undefined') return;
    const canvas = $('[data-progress-chart]');
    if (!canvas) return;
    if (progressChart.instance) progressChart.instance.destroy();

    Chart.defaults.color = getComputedStyle(document.body).getPropertyValue('--fg-muted') || '#888';
    progressChart.instance = new Chart(canvas, {
      type: 'line',
      data: {
        labels: data.points.map((p) => new Date(p.day).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })),
        datasets: [{
          label: 'Общий балл',
          data: data.points.map((p) => p.overall_score),
          borderColor: '#4fd1c5',
          backgroundColor: 'rgba(79, 209, 197, .16)',
          fill: true,
          tension: 0.3,
          spanGaps: true,
          pointRadius: 3,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { min: 0, max: 10, grid: { color: 'rgba(128,128,128,.15)' } },
          x: { grid: { display: false } },
        },
        plugins: { legend: { display: false } },
      },
    });
  };

  const renderBeforeAfter = (data) => {
    const box = $('[data-before-after]');
    if (!box) return;
    if (!data) {
      box.innerHTML = '<p class="muted">Загрузите и оцените минимум два фото, чтобы увидеть динамику.</p>';
      return;
    }
    const deltaColor = data.overall_delta >= 0 ? 'var(--ok)' : 'var(--danger)';
    const rows = data.metrics
      .map((m) => `
        <tr>
          <td>${m.label}</td>
          <td>${m.before.toFixed(1)}</td>
          <td>${m.after.toFixed(1)}</td>
          <td style="color:${m.delta >= 0 ? 'var(--ok)' : 'var(--danger)'}">
            ${m.delta > 0 ? '+' : ''}${m.delta.toFixed(2)}
          </td>
        </tr>`)
      .join('');

    box.innerHTML = `
      <div class="ba-summary">
        <div class="ba-box">
          <span class="muted small">Первый отчёт</span>
          <span class="ba-score">${data.before_overall.toFixed(1)}</span>
          <span class="muted small">${new Date(data.before_date).toLocaleDateString('ru-RU')}</span>
        </div>
        <div class="ba-arrow" style="color:${deltaColor}">
          ${data.overall_delta > 0 ? '+' : ''}${data.overall_delta.toFixed(2)}
        </div>
        <div class="ba-box">
          <span class="muted small">Последний отчёт</span>
          <span class="ba-score">${data.after_overall.toFixed(1)}</span>
          <span class="muted small">${new Date(data.after_date).toLocaleDateString('ru-RU')}</span>
        </div>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead><tr><th>Метрика</th><th>Было</th><th>Стало</th><th>Δ</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  };

  const renderAchievements = (items) => {
    const box = $('[data-achievements]');
    if (!box) return;
    box.innerHTML = items
      .map((a) => `
        <div class="achievement ${a.earned ? 'is-earned' : ''}">
          <div class="achievement-icon" aria-hidden="true">${a.earned ? '★' : '☆'}</div>
          <div class="achievement-body">
            <strong>${a.title}</strong>
            <p class="muted small">${a.description}</p>
            ${a.earned
              ? `<span class="achievement-date small">получено ${new Date(a.awarded_at).toLocaleDateString('ru-RU')}</span>`
              : `<div class="progress"><i style="width:${Math.round(a.progress * 100)}%"></i></div>`}
          </div>
        </div>`)
      .join('');
  };

  const renderReports = (items) => {
    const box = $('[data-reports-list]');
    if (!box) return;
    if (!items.length) {
      box.innerHTML = '<p class="muted">Отчётов пока нет. <a href="/rate">Сделайте первый</a>.</p>';
      return;
    }
    box.innerHTML = items
      .map((r) => `
        <a class="report-row" href="/report?id=${r.id}">
          <span class="report-score" style="color:${scoreColor(r.overall_score)}">${r.overall_score.toFixed(1)}</span>
          <span class="report-date muted small">${new Date(r.created_at).toLocaleDateString('ru-RU')}</span>
          <span class="report-open">Открыть →</span>
        </a>`)
      .join('');
  };

  const loadProgress = async (days) => {
    try {
      const data = await api(`/api/profile/progress?days=${days}`);
      renderProgress(data);
    } catch (err) {
      toast(err.message || 'Не удалось загрузить прогресс', 'error');
    }
  };

  const loadAll = async () => {
    try {
      const [profile, achievements, reports, beforeAfter] = await Promise.all([
        api('/api/profile'),
        api('/api/profile/achievements'),
        api('/api/reports?limit=10'),
        api('/api/profile/before-after').catch(() => null),
      ]);
      renderProfile(profile);
      renderAchievements(achievements);
      renderReports(reports.items);
      renderBeforeAfter(beforeAfter);
      await loadProgress(Number($('[data-progress-range]').value) || 30);
    } catch (err) {
      toast(err.message || 'Не удалось загрузить профиль', 'error');
    }
  };

  const rangeSelect = $('[data-progress-range]');
  if (rangeSelect) rangeSelect.addEventListener('change', () => loadProgress(Number(rangeSelect.value)));

  const modal = $('[data-profile-modal]');
  const editBtn = $('[data-edit-profile]');
  if (editBtn && modal) editBtn.addEventListener('click', () => modal.showModal());

  const form = $('[data-profile-form]');
  if (form) {
    form.addEventListener('submit', async (event) => {
      if (event.submitter && event.submitter.value !== 'save') return;
      event.preventDefault();

      const payload = {};
      if (form.display_name.value.trim()) payload.display_name = form.display_name.value.trim();
      if (form.gender.value) payload.gender = form.gender.value;
      if (form.birth_year.value) payload.birth_year = Number(form.birth_year.value);
      payload.show_in_battles = form.show_in_battles.checked;

      try {
        const profile = await api('/api/profile', { method: 'PATCH', body: JSON.stringify(payload) });
        renderProfile(profile);
        modal.close();
        toast('Профиль обновлён', 'success');
      } catch (err) {
        toast(err.message || 'Не удалось сохранить профиль', 'error');
      }
    });
  }

  loadAll();
})();

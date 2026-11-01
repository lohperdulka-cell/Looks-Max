/* ==========================================================================
   FaceMetric — страница отчёта: ring, графики Chart.js, таблица, рекомендации.
   ========================================================================== */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const api = window.fmApi;
  const toast = window.fmToast;
  if (!api || !$('[data-report-root]')) return;

  const params = new URLSearchParams(window.location.search);
  // Публичный режим: /r/{token}. Владельческий: /report?id={id}.
  const publicMatch = window.location.pathname.match(/^\/r\/([A-Za-z0-9_-]+)/);
  const publicToken = publicMatch ? publicMatch[1] : null;
  const reportId = params.get('id') || window.location.pathname.split('/').pop();
  const isPublic = Boolean(publicToken);

  const loading = $('[data-report-loading]');
  const root = $('[data-report-root]');
  const charts = {};

  const scoreColor = (score) => (score >= 7.5 ? '#16a34a' : score >= 5 ? '#ca8a04' : '#dc2626');

  const metricItem = (m) => `
    <li class="metric-item">
      <div class="metric-item-head">
        <span class="metric-item-label">${m.label}</span>
        <span class="metric-item-score" style="color:${scoreColor(m.score)}">${m.score.toFixed(1)}</span>
      </div>
      <div class="progress"><i style="width:${m.score * 10}%"></i></div>
    </li>`;

  const renderCharts = (data) => {
    if (typeof Chart === 'undefined') return;
    Chart.defaults.color = getComputedStyle(document.body).getPropertyValue('--fg-muted') || '#888';
    Chart.defaults.font.family = 'inherit';

    const catCanvas = $('[data-category-chart]');
    if (catCanvas && data.categories_detail.length) {
      charts.categories = new Chart(catCanvas, {
        type: 'bar',
        data: {
          labels: data.categories_detail.map((c) => c.label),
          datasets: [{
            label: 'Балл',
            data: data.categories_detail.map((c) => c.score),
            backgroundColor: data.categories_detail.map((c) => `${scoreColor(c.score)}cc`),
            borderRadius: 6,
          }],
        },
        options: {
          indexAxis: 'y',
          responsive: true,
          maintainAspectRatio: false,
          scales: { x: { min: 0, max: 10, grid: { display: false } }, y: { grid: { display: false } } },
          plugins: { legend: { display: false } },
        },
      });
    }

    const radarCanvas = $('[data-radar-chart]');
    if (radarCanvas && data.metrics.length) {
      const maxMetrics = 12;
      const metrics = [...data.metrics].sort((a, b) => b.score - a.score).slice(0, maxMetrics);
      charts.radar = new Chart(radarCanvas, {
        type: 'radar',
        data: {
          labels: metrics.map((m) => m.label),
          datasets: [{
            label: 'Балл',
            data: metrics.map((m) => m.score),
            borderColor: '#4fd1c5',
            backgroundColor: 'rgba(79, 209, 197, .22)',
            pointBackgroundColor: '#4fd1c5',
            borderWidth: 2,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: { r: { min: 0, max: 10, ticks: { stepSize: 2, backdropColor: 'transparent' } } },
          plugins: { legend: { display: false } },
        },
      });
    }
  };

  const renderTable = (metrics) => {
    const tbody = $('[data-metrics-table] tbody');
    if (!tbody) return;
    tbody.innerHTML = metrics
      .map((m) => `
        <tr>
          <td>${m.label}</td>
          <td class="muted">${m.category_label}</td>
          <td>${m.raw_value === null ? '—' : m.raw_value.toFixed(3)} ${m.unit || ''}</td>
          <td class="muted">${m.reference_value === null ? '—' : m.reference_value.toFixed(3)}</td>
          <td style="color:${scoreColor(m.score)};font-weight:600">${m.score.toFixed(1)}</td>
        </tr>`)
      .join('');
  };

  const renderRecommendations = (items) => {
    const box = $('[data-recommendations]');
    if (!box) return;
    if (!items.length) {
      box.innerHTML = '<p class="muted">Явных зон роста не выявлено.</p>';
      return;
    }
    box.innerHTML = items
      .map((r) => `
        <div class="rec-card is-${r.severity}">
          <div class="rec-head">
            <h3>${r.priority}. ${r.label}</h3>
            <span class="badge badge-${r.severity}">${r.severity === 'high' ? 'важно' : r.severity === 'medium' ? 'средне' : 'мягко'}</span>
          </div>
          <p>${r.text}</p>
          <ul class="rec-actions">${r.actions.map((a) => `<li>${a}</li>`).join('')}</ul>
          <p class="rec-source muted small">Источник: ${r.source}</p>
        </div>`)
      .join('');
  };

  const updateShareBox = (report) => {
    const box = $('[data-share-box]');
    const input = $('[data-share-url]');
    if (!box || !input) return;
    if (report.is_public && report.public_token) {
      box.hidden = false;
      input.value = `${window.location.origin}/r/${report.public_token}`;
    } else {
      box.hidden = true;
      input.value = '';
    }
  };

  const render = (report) => {
    loading.hidden = true;
    root.hidden = false;

    if (isPublic) {
      document.querySelector('.report-actions').hidden = true;
      document.querySelector('[data-share-box]').hidden = true;
    }

    $('[data-overall-value]').textContent = report.overall_score.toFixed(1);
    const ring = $('[data-overall-ring]');
    const pct = Math.max(0, Math.min(100, report.overall_score * 10));
    ring.style.background = `conic-gradient(${scoreColor(report.overall_score)} 0 ${pct}%, var(--surface-2) ${pct}% 100%)`;
    $('[data-harmony]').textContent =
      `Гармония: ${(report.harmony_score || 0).toFixed(1)} · модель ${report.model_version}`;
    $('[data-report-meta]').textContent = isPublic
      ? `Публичный отчёт · ${new Date(report.created_at).toLocaleDateString('ru-RU')}`
      : `Отчёт #${report.id} · ${new Date(report.created_at).toLocaleDateString('ru-RU')}`;

    $('[data-strengths]').innerHTML = report.strengths.map(metricItem).join('') || '<li class="muted">—</li>';
    $('[data-growth]').innerHTML = report.growth.map(metricItem).join('') || '<li class="muted">—</li>';
    $('[data-disclaimer]').textContent = report.disclaimer;
    if (report.is_demo) {
      const note = document.createElement('p');
      note.className = 'demo-note';
      note.textContent = 'Демо-режим: точки лица синтетические, баллы не описывают внешность.';
      $('[data-disclaimer]').before(note);
    }

    renderTable(report.metrics);
    renderRecommendations(report.recommendations);
    renderCharts(report);
    updateShareBox(report);

    if (isPublic) return;
    const pdf = $('[data-export-pdf]');
    const html = $('[data-export-html]');
    if (pdf) pdf.href = `/api/reports/${report.id}/export/pdf`;
    if (html) html.href = `/api/reports/${report.id}/export/html`;
  };

  const toggleBtn = $('[data-toggle-share]');
  if (toggleBtn) toggleBtn.addEventListener('click', async () => {
    const toggle = toggleBtn;
    toggle.setAttribute('aria-busy', 'true');
    try {
      const current = await api(`/api/reports/${reportId}`);
      const next = await api(`/api/reports/${reportId}/share`, {
        method: 'PATCH',
        body: JSON.stringify({ is_public: !current.is_public }),
      });
      updateShareBox(next);
      toast(next.is_public ? 'Публичная ссылка включена' : 'Публичная ссылка выключена', 'success');
    } catch (err) {
      toast(err.message || 'Не удалось изменить доступ', 'error');
    } finally {
      toggle.removeAttribute('aria-busy');
    }
  });

  const copyBtn = $('[data-copy-share]');
  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      const input = $('[data-share-url]');
      try {
        await navigator.clipboard.writeText(input.value);
        toast('Ссылка скопирована', 'success');
      } catch {
        input.select();
        toast('Скопируйте ссылку вручную', 'info');
      }
    });
  }

  (async () => {
    try {
      const url = isPublic ? `/api/public/reports/${publicToken}` : `/api/reports/${reportId}`;
      const report = await api(url);
      if (isPublic) report.id = publicToken;
      render(report);
    } catch (err) {
      loading.hidden = true;
      root.hidden = false;
      root.innerHTML = `<div class="empty-state"><h2>Отчёт не найден</h2>
        <p class="muted">${err.message || 'Проверьте ссылку или создайте новый отчёт.'}</p>
        <a class="btn btn-primary" href="/rate">Перейти к оценке</a></div>`;
    }
  })();
})();

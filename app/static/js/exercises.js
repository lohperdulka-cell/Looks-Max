/* ==========================================================================
   FaceMetric — библиотека упражнений: фильтры, календарь, отметки выполнения.
   ========================================================================== */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const api = window.fmApi;
  const toast = window.fmToast;
  const escapeHtml = window.fmEscape;
  if (!api || !$('[data-ex-list]')) return;

  const state = { category: 'all', items: [], selected: null };

  const difficultyLabel = { easy: 'легко', medium: 'средне', hard: 'сложно' };

  const exerciseCard = (ex) => `
    <article class="ex-card card ${ex.completed_today ? 'is-done' : ''}" data-slug="${ex.slug}">
      <header class="ex-card-head">
        <h3>${escapeHtml(ex.title)}</h3>
        <span class="ex-badge">${escapeHtml(ex.category_label)}</span>
      </header>
      <p class="muted small">${escapeHtml(ex.description)}</p>
      <footer class="ex-card-foot">
        <span class="muted small">${difficultyLabel[ex.difficulty] || ex.difficulty} · ${Math.round((ex.duration_seconds || 0) / 60)} мин</span>
        <span class="ex-count muted small">${ex.completions_count} раз</span>
        <button class="btn ${ex.completed_today ? 'btn-ghost' : 'btn-primary'} btn-sm" type="button" data-ex-open="${ex.slug}">
          ${ex.completed_today ? 'Выполнено сегодня' : 'Открыть'}
        </button>
      </footer>
    </article>`;

  const render = () => {
    const list = $('[data-ex-list]');
    const items = state.category === 'all'
      ? state.items
      : state.items.filter((ex) => ex.category === state.category);

    list.innerHTML = items.length
      ? items.map(exerciseCard).join('')
      : '<p class="muted">В этой категории пока нет упражнений.</p>';

    $$('[data-ex-open]').forEach((btn) => {
      btn.addEventListener('click', () => openModal(btn.dataset.exOpen));
    });
  };

  const renderStats = (payload) => {
    const done = payload.items.filter((ex) => ex.completed_today).length;
    $('[data-ex-streak]').textContent = payload.streak;
    $('[data-ex-total]').textContent = payload.total_completions;
    $('[data-ex-today]').textContent = done;
  };

  const renderCalendar = (payload) => {
    const grid = $('[data-ex-grid]');
    grid.innerHTML = payload.calendar
      .map((day) => {
        const level = Math.min(day.completed, 4);
        const title = `${new Date(day.day).toLocaleDateString('ru-RU')}: ${day.completed}`;
        return `<span class="ex-cell level-${level}" title="${title}"></span>`;
      })
      .join('');
  };

  const renderRecommended = (payload) => {
    if (!payload.items.length) return;
    $('[data-ex-recommended]').hidden = false;
    $('[data-ex-rec-list]').innerHTML = payload.items.map(exerciseCard).join('');
    $$('[data-ex-rec-list] [data-ex-open]').forEach((btn) => {
      btn.addEventListener('click', () => openModal(btn.dataset.exOpen));
    });
  };

  const openModal = (slug) => {
    const ex = state.items.find((item) => item.slug === slug);
    if (!ex) return;
    state.selected = ex;
    $('[data-ex-modal-title]').textContent = ex.title;
    $('[data-ex-modal-body]').innerHTML = `
      <p>${escapeHtml(ex.description)}</p>
      <p class="muted small">Категория: ${escapeHtml(ex.category_label)} · ${difficultyLabel[ex.difficulty] || ex.difficulty}</p>
      <pre class="ex-instructions">${escapeHtml(ex.instructions || '')}</pre>
      ${ex.targets.length ? `<p class="muted small">Влияет на метрики: ${ex.targets.map(escapeHtml).join(', ')}</p>` : ''}`;
    const completeBtn = $('[data-ex-complete]');
    completeBtn.textContent = ex.completed_today ? 'Уже отмечено сегодня' : 'Отметить выполнение';
    completeBtn.disabled = ex.completed_today;
    $('[data-ex-modal]').showModal();
  };

  const complete = async () => {
    if (!state.selected || state.selected.completed_today) return;
    try {
      const result = await api(`/api/exercises/${state.selected.slug}/complete`, {
        method: 'POST',
        body: JSON.stringify({}),
      });
      toast(
        result.created
          ? `Готово! Серия: ${result.streak} дн.`
          : 'Уже отмечено сегодня',
        'success',
      );
      $('[data-ex-modal]').close();
      await load();
    } catch (err) {
      toast(err.message || 'Не удалось отметить', 'error');
    }
  };

  const load = async () => {
    try {
      const [list, progress, recommended] = await Promise.all([
        api('/api/exercises'),
        api('/api/exercises/progress?days=30'),
        api('/api/exercises/recommended'),
      ]);
      state.items = list.items;
      render();
      renderStats(list);
      renderCalendar(progress);
      renderRecommended(recommended);
    } catch (err) {
      toast(err.message || 'Не удалось загрузить упражнения', 'error');
    }
  };

  $$('[data-category]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.category = btn.dataset.category;
      $$('[data-category]').forEach((b) => b.classList.toggle('is-active', b === btn));
      render();
    });
  });

  $('[data-ex-modal-close]').addEventListener('click', () => $('[data-ex-modal]').close());
  $('[data-ex-complete]').addEventListener('click', complete);
  load();
})();

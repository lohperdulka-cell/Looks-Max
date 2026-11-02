/* ==========================================================================
   FaceMetric — страница оценки: загрузка фото, качество, предпросмотр метрик
   и сохранение отчёта.

   Совмещение лица и разметку точек делает «студия» (studio.js) в модальном
   окне на весь экран. Здесь остаётся то, что живёт на самой странице: выбор
   файла, оценка качества, пол для эталонов, показ метрик и создание отчёта.

   Раньше этот файл ещё и рисовал canvas с точками, из-за чего состояние точек
   дублировалось между страницей и мастером. Теперь источник точек один —
   разметка, сохранённая студией на бэкенде.
   ========================================================================== */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const api = window.fmApi;
  const toast = window.fmToast;
  if (!api) return;

  const escapeHtml = (s) =>
    String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const dropzone = $('[data-dropzone]');
  const fileInput = $('[data-file-input]');
  const progress = $('[data-upload-progress]');
  const progressBar = $('[data-upload-progress] > i');
  const steps = $('[data-upload-steps]');
  const qualityBox = $('[data-quality]');
  const genderPick = $('[data-gender-pick]');
  const previewEmpty = $('[data-preview-empty]');
  const previewContent = $('[data-preview-content]');
  const runBtn = $('[data-run-preview]');
  const editBtn = $('[data-open-studio]');
  const reportBtn = $('[data-create-report]');

  const state = { photo: null, preview: null };

  const hasStudio = () => Boolean(window.fmStudio);
  if (!dropzone) return;

  /* --- Индикатор загрузки --------------------------------------------------- */

  const setStep = (name, status) => {
    const li = steps && $(`[data-step="${name}"]`, steps);
    if (li) li.className = `is-${status}`;
  };

  const setProgress = (value) => {
    if (!progress || !progressBar) return;
    progress.hidden = false;
    progressBar.style.width = `${Math.round(value * 100)}%`;
    if (value >= 1) setTimeout(() => { progress.hidden = true; }, 600);
  };

  /* --- Пол для эталонов ----------------------------------------------------- */

  const syncGenderChips = (gender) => {
    if (!genderPick) return;
    $$('.gender-chip', genderPick).forEach((btn) => {
      const active = btn.dataset.gender === gender;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-checked', String(active));
    });
  };

  const saveGender = async (gender) => {
    try {
      await api('/api/profile', { method: 'PATCH', body: { gender } });
      syncGenderChips(gender);
    } catch (err) {
      toast(err.message || 'Не удалось сохранить пол', 'error');
    }
  };

  if (genderPick) {
    genderPick.addEventListener('click', (e) => {
      const btn = e.target.closest('.gender-chip');
      if (btn) saveGender(btn.dataset.gender);
    });
  }

  const loadGender = async () => {
    try {
      const profile = await api('/api/profile');
      syncGenderChips(profile.gender || 'other');
    } catch { /* гость — пол не важен */ }
  };

  /* --- Загрузка файла ------------------------------------------------------- */

  const pickFile = () => fileInput.click();
  dropzone.addEventListener('click', pickFile);
  dropzone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pickFile(); }
  });
  fileInput.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    if (file) uploadFile(file);
    fileInput.value = '';
  });
  ['dragenter', 'dragover'].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add('is-dragover');
    }));
  ['dragleave', 'drop'].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove('is-dragover');
    }));
  dropzone.addEventListener('drop', (e) => {
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (file) uploadFile(file);
  });

  const validateClientSide = (file) => {
    const allowed = ['image/jpeg', 'image/png', 'image/webp'];
    if (!allowed.includes(file.type)) return 'Поддерживаются только JPEG, PNG и WebP.';
    if (file.size > 12 * 1024 * 1024) return 'Файл больше 12 МБ.';
    return null;
  };

  const uploadFile = async (file) => {
    const clientError = validateClientSide(file);
    if (clientError) { toast(clientError, 'error'); return; }

    steps.hidden = false;
    setStep('upload', 'active');
    setStep('detect', 'idle');
    setStep('align', 'idle');
    setStep('quality', 'idle');
    setProgress(0.15);

    const form = new FormData();
    form.append('file', file);

    try {
      setProgress(0.35);
      const csrf = window.fm.getCookie('fm_csrf');
      const resp = await fetch('/api/photos', {
        method: 'POST',
        body: form,
        credentials: 'same-origin',
        headers: csrf ? { 'X-CSRF-Token': csrf } : {},
      });
      const text = await resp.text();
      const data = text ? JSON.parse(text) : null;
      if (!resp.ok) throw new Error((data && data.detail) || `HTTP ${resp.status}`);

      setStep('upload', 'done');
      setStep('detect', 'done');
      setStep('align', 'done');
      setStep('quality', 'done');
      setProgress(1);

      state.photo = data;
      renderQuality(data.warnings || []);
      setActionsEnabled(true);
      resetPreview();
      toast('Фото загружено', 'success');
      // Сразу открываем студию: пользователь не должен искать кнопку.
      openStudio();
    } catch (err) {
      setStep('upload', 'done');
      setProgress(0);
      toast(err.message || 'Не удалось загрузить фото', 'error');
    }
  };

  const setActionsEnabled = (on) => {
    if (runBtn) runBtn.disabled = !on;
    if (reportBtn) reportBtn.disabled = !on;
    if (editBtn) editBtn.disabled = !on || !hasStudio();
  };

  /* --- Качество фото -------------------------------------------------------- */

  const renderQuality = (warnings) => {
    if (!qualityBox || !state.photo) return;
    const pct = Math.round((state.photo.quality_score || 0) * 100);
    const level = pct >= 75 ? 'ok' : pct >= 50 ? 'warn' : 'bad';
    const label = pct >= 75 ? 'Хорошее качество' : pct >= 50 ? 'Среднее качество' : 'Низкое качество';
    qualityBox.hidden = false;
    qualityBox.className = `quality-report is-${level}`;
    qualityBox.innerHTML =
      (state.photo.is_demo
        ? '<p class="demo-note">Демо-режим: детектор лица не установлен, точки синтетические.</p>'
        : '') +
      `<div class="quality-head"><strong>${escapeHtml(label)}</strong><span>${pct}%</span></div>` +
      `<div class="progress"><i style="width:${pct}%"></i></div>` +
      (warnings.length
        ? `<ul class="quality-warnings">${warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join('')}</ul>`
        : '');
  };

  /* --- Предпросмотр метрик -------------------------------------------------- */

  const resetPreview = () => {
    state.preview = null;
    if (previewEmpty) previewEmpty.hidden = false;
    if (previewContent) previewContent.hidden = true;
  };

  const metricItem = (m) => {
    const score = Math.max(0, Math.min(10, m.score));
    return `<li class="metric-item">
      <div class="metric-item-head">
        <span class="metric-item-label">${escapeHtml(m.label)}</span>
        <span class="metric-item-score">${score.toFixed(1)}</span>
      </div>
      <div class="progress"><i style="width:${score * 10}%"></i></div>
      ${m.notes ? `<p class="metric-item-note">${escapeHtml(m.notes)}</p>` : ''}
    </li>`;
  };

  const renderPreview = (data) => {
    if (previewEmpty) previewEmpty.hidden = true;
    if (previewContent) previewContent.hidden = false;

    const overall = $('[data-overall-value]');
    if (overall) overall.textContent = data.overall.toFixed(1);

    const bars = $('[data-category-bars]');
    if (bars) {
      bars.innerHTML = Object.entries(data.categories)
        .map(([key, value]) => `
          <div class="category-bar">
            <span class="category-name">${escapeHtml(key)}</span>
            <div class="progress"><i style="width:${value * 10}%"></i></div>
            <span class="category-value">${value.toFixed(1)}</span>
          </div>`)
        .join('');
    }

    const strengths = $('[data-strengths]');
    if (strengths) strengths.innerHTML = data.strengths.map(metricItem).join('') || '<li class="muted">—</li>';
    const growth = $('[data-growth]');
    if (growth) growth.innerHTML = data.growth.map(metricItem).join('') || '<li class="muted">—</li>';
    const disclaimer = $('[data-disclaimer]');
    if (disclaimer) disclaimer.textContent = data.disclaimer;
  };

  const runPreview = async () => {
    if (!state.photo || !runBtn) return;
    runBtn.setAttribute('aria-busy', 'true');
    runBtn.disabled = true;
    try {
      const data = await api(`/api/photos/${state.photo.id}/preview`, { method: 'POST' });
      state.preview = data;
      renderPreview(data);
      $('[data-preview-panel]').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (err) {
      toast(err.message || 'Не удалось рассчитать метрики', 'error');
    } finally {
      runBtn.removeAttribute('aria-busy');
      runBtn.disabled = false;
    }
  };

  if (runBtn) runBtn.addEventListener('click', runPreview);

  /* --- Студия --------------------------------------------------------------- */

  const openStudio = () => {
    if (!state.photo || !hasStudio()) return;
    window.fmStudio.open(state.photo.id, {
      onDone: async () => {
        // Разметка сохранена студией — сразу показываем метрики.
        setActionsEnabled(true);
        await runPreview();
      },
    });
  };

  if (editBtn) editBtn.addEventListener('click', openStudio);

  /* --- Отчёт ---------------------------------------------------------------- */

  if (reportBtn) {
    reportBtn.addEventListener('click', async () => {
      if (!state.photo) return;
      reportBtn.setAttribute('aria-busy', 'true');
      reportBtn.disabled = true;
      try {
        const csrf = window.fm.getCookie('fm_csrf');
        const report = await api(`/api/reports?photo_id=${state.photo.id}`, {
          method: 'POST',
          headers: csrf ? { 'X-CSRF-Token': csrf } : {},
        });
        toast('Отчёт сохранён', 'success');
        window.location.href = `/report?id=${report.id}`;
      } catch (err) {
        toast(err.message || 'Не удалось создать отчёт', 'error');
        reportBtn.removeAttribute('aria-busy');
        reportBtn.disabled = false;
      }
    });
  }

  /* --- Восстановление последнего фото -------------------------------------- */

  (async () => {
    await loadGender();
    setActionsEnabled(false);
    try {
      const list = await api('/api/photos?limit=1');
      if (!list.items || !list.items.length) return;
      const detail = await api(`/api/photos/${list.items[0].id}`);
      state.photo = detail;
      renderQuality([]);
      setActionsEnabled(Boolean(detail.landmarks && detail.landmarks.points));
    } catch { /* гость или пусто — ничего не делаем */ }
  })();
})();

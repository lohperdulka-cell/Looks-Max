/* ==========================================================================
   FaceMetric — страница оценки: загрузка фото, canvas-редактор точек,
   предпросмотр метрик.
   ========================================================================== */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const api = window.fmApi;
  const toast = window.fmToast;

  if (!api || !$('[data-dropzone]')) return;

  const dropzone = $('[data-dropzone]');
  const fileInput = $('[data-file-input]');
  const progress = $('[data-upload-progress]');
  const progressBar = $('[data-upload-progress] > i');
  const steps = $('[data-upload-steps]');
  const qualityBox = $('[data-quality]');
  const canvas = $('[data-canvas]');
  const canvasHint = $('[data-canvas-hint]');
  const runBtn = $('[data-run-preview]');
  const resetBtn = $('[data-reset-points]');
  const reportBtn = $('[data-create-report]');
  const previewEmpty = $('[data-preview-empty]');
  const previewContent = $('[data-preview-content]');
  const genderPick = $('[data-gender-pick]');

  const ctx = canvas.getContext('2d');

  /** Состояние страницы. */
  const state = {
    photo: null,          // метаданные фото с сервера
    image: null,          // HTMLImageElement
    points: [],           // [{x, y}] в нормализованных координатах 0..1
    originalPoints: [],   // для сброса
    dragging: -1,         // индекс перетаскиваемой точки
    dirty: false,
    objectUrl: null,
  };

  const setStep = (name, status) => {
    const li = steps && $(`[data-step="${name}"]`, steps);
    if (!li) return;
    li.classList.toggle('is-active', status === 'active');
    li.classList.toggle('is-done', status === 'done');
  };

  const setProgress = (value) => {
    if (!progress) return;
    progress.hidden = value <= 0;
    if (progressBar) progressBar.style.width = `${Math.round(value * 100)}%`;
    if (value >= 1) setTimeout(() => { progress.hidden = true; }, 600);
  };

  /* --- Пол для эталонов ---------------------------------------------------- */
  // MediaPipe не предсказывает пол, поэтому эталоны диморфизма берутся из
  // профиля. Кнопки позволяют задать пол, не уходя со страницы оценки.
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
      const profile = await api('/api/profile', {
        method: 'PATCH',
        body: { gender },
      });
      syncGenderChips(profile.gender || '');
      resetPreview();
      toast('Пол сохранён: эталоны метрик обновятся при следующем расчёте.', 'success');
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
    if (!genderPick) return;
    try {
      const profile = await api('/api/profile');
      syncGenderChips(profile.gender || '');
    } catch { /* гость — кнопки остаются неактивными */ }
  };

  /* --- Загрузка файла ------------------------------------------------------ */
  const pickFile = () => fileInput.click();

  dropzone.addEventListener('click', pickFile);
  dropzone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pickFile(); }
  });
  fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files[0]) uploadFile(fileInput.files[0]);
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
      state.points = (data.landmarks && data.landmarks.points || []).map(([x, y]) => ({ x, y }));
      state.originalPoints = state.points.map((p) => ({ ...p }));
      state.dirty = false;

      await loadImage(data.image_url);
      renderQuality(data.warnings || []);
      runBtn.disabled = state.points.length === 0;
      resetBtn.disabled = state.points.length === 0;
      if (reportBtn) reportBtn.disabled = state.points.length === 0;
      toast('Фото обработано. Точки можно поправить.', 'success');
      resetPreview();
    } catch (err) {
      setStep('upload', 'done');
      setProgress(0);
      toast(err.message || 'Не удалось загрузить фото', 'error');
    }
  };

  const loadImage = (url) =>
    new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
        state.image = img;
        resizeCanvasToImage(img);
        if (canvasHint) canvasHint.hidden = true;
        // Анимация «сканирования»: точки проявляются за линией сканера,
        // затем соединяются линиями. После неё — обычный режим редактора.
        if (window.fmScan && state.points.length) {
          window.fmScan.playScan(canvas, img, state.points, {
            onDone: () => draw(),
          });
        } else {
          draw();
        }
        resolve(img);
      };
      img.onerror = () => reject(new Error('Не удалось отобразить изображение'));
      img.src = url;
    });

  const resizeCanvasToImage = (img) => {
    const wrapWidth = canvas.parentElement.clientWidth || 520;
    const ratio = img.naturalHeight / img.naturalWidth;
    canvas.width = Math.min(wrapWidth, 640);
    canvas.height = Math.round(canvas.width * ratio);
  };

  /* --- Отрисовка canvas ---------------------------------------------------- */
  const draw = () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!state.image) return;

    ctx.drawImage(state.image, 0, 0, canvas.width, canvas.height);

    if (state.points.length) {
      // Линии, помогающие оценить симметрию и трети.
      ctx.save();
      ctx.strokeStyle = 'rgba(79, 209, 197, .55)';
      ctx.lineWidth = 1;
      ctx.setLineDash([5, 5]);
      ctx.beginPath();
      ctx.moveTo(0, canvas.height / 2);
      ctx.lineTo(canvas.width, canvas.height / 2);
      ctx.stroke();
      ctx.restore();

      state.points.forEach((p, i) => {
        const x = p.x * canvas.width;
        const y = p.y * canvas.height;
        ctx.beginPath();
        ctx.arc(x, y, i === state.dragging ? 8 : 5.5, 0, Math.PI * 2);
        ctx.fillStyle = i === state.dragging ? '#7c9cff' : '#4fd1c5';
        ctx.fill();
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = 'rgba(11, 15, 20, .85)';
        ctx.stroke();
      });
    }
  };

  /* --- Перетаскивание точек ------------------------------------------------ */
  const hitTest = (px, py) => {
    const threshold = 14;
    let best = -1;
    let bestDist = Infinity;
    state.points.forEach((p, i) => {
      const dx = p.x * canvas.width - px;
      const dy = p.y * canvas.height - py;
      const dist = Math.hypot(dx, dy);
      if (dist < threshold && dist < bestDist) { best = i; bestDist = dist; }
    });
    return best;
  };

  const eventPos = (e) => {
    const rect = canvas.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    return {
      x: (clientX - rect.left) * (canvas.width / rect.width),
      y: (clientY - rect.top) * (canvas.height / rect.height),
    };
  };

  const onDown = (e) => {
    if (!state.points.length) return;
    const { x, y } = eventPos(e);
    state.dragging = hitTest(x, y);
    if (state.dragging >= 0) {
      e.preventDefault();
      canvas.style.cursor = 'grabbing';
      draw();
    }
  };

  const onMove = (e) => {
    if (!state.points.length) return;
    const { x, y } = eventPos(e);
    if (state.dragging >= 0) {
      e.preventDefault();
      state.points[state.dragging] = {
        x: Math.min(1, Math.max(0, x / canvas.width)),
        y: Math.min(1, Math.max(0, y / canvas.height)),
      };
      state.dirty = true;
      draw();
    } else {
      canvas.style.cursor = hitTest(x, y) >= 0 ? 'grab' : 'default';
    }
  };

  const onUp = () => {
    if (state.dragging >= 0) {
      state.dragging = -1;
      canvas.style.cursor = 'default';
      draw();
    }
  };

  canvas.addEventListener('mousedown', onDown);
  canvas.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);
  canvas.addEventListener('touchstart', onDown, { passive: false });
  canvas.addEventListener('touchmove', onMove, { passive: false });
  window.addEventListener('touchend', onUp);

  window.addEventListener('resize', () => {
    if (state.image) { resizeCanvasToImage(state.image); draw(); }
  });

  /* --- Качество фото ------------------------------------------------------- */
  const renderQuality = (warnings) => {
    if (!qualityBox) return;
    const quality = state.photo && state.photo.quality_score;
    const pct = Math.round((quality || 0) * 100);
    const level = pct >= 75 ? 'ok' : pct >= 50 ? 'warn' : 'bad';
    const label = pct >= 75 ? 'Хорошее качество' : pct >= 50 ? 'Среднее качество' : 'Низкое качество';
    qualityBox.hidden = false;
    qualityBox.className = `quality-report is-${level}`;
    qualityBox.innerHTML =
      (state.photo && state.photo.is_demo
        ? '<p class="demo-note">Демо-режим: детектор лица не установлен, точки синтетические.</p>'
        : '') +
      `<div class="quality-head"><strong>${label}</strong><span>${pct}%</span></div>` +
      `<div class="progress"><i style="width:${pct}%"></i></div>` +
      (warnings.length
        ? `<ul class="quality-warnings">${warnings.map((w) => `<li>${w}</li>`).join('')}</ul>`
        : '');
  };

  /* --- Предпросмотр метрик ------------------------------------------------- */
  const resetPreview = () => {
    if (previewEmpty) previewEmpty.hidden = false;
    if (previewContent) previewContent.hidden = true;
  };

  const metricItem = (m) => {
    const score = Math.max(0, Math.min(10, m.score));
    return `<li class="metric-item">
      <div class="metric-item-head">
        <span class="metric-item-label">${m.label}</span>
        <span class="metric-item-score">${score.toFixed(1)}</span>
      </div>
      <div class="progress"><i style="width:${score * 10}%"></i></div>
      ${m.notes ? `<p class="metric-item-note">${m.notes}</p>` : ''}
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
            <span class="category-name">${key}</span>
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

  const savePoints = async () => {
    const csrf = window.fm.getCookie('fm_csrf');
    await fetch(`/api/photos/${state.photo.id}/landmarks`, {
      method: 'PUT',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
      },
      body: JSON.stringify({
        points: state.points.map((p) => [p.x, p.y]),
        provider: 'manual',
        bbox: (state.photo.landmarks && state.photo.landmarks.bbox) || null,
      }),
    });
    state.dirty = false;
  };

  runBtn.addEventListener('click', async () => {
    if (!state.photo) return;
    runBtn.setAttribute('aria-busy', 'true');
    runBtn.disabled = true;
    try {
      if (state.dirty) await savePoints();
      const data = await api(`/api/photos/${state.photo.id}/preview`, { method: 'POST' });
      renderPreview(data);
      $('[data-preview-panel]').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (err) {
      toast(err.message || 'Не удалось рассчитать метрики', 'error');
    } finally {
      runBtn.removeAttribute('aria-busy');
      runBtn.disabled = false;
    }
  });

  if (reportBtn) {
    reportBtn.addEventListener('click', async () => {
      if (!state.photo) return;
      reportBtn.setAttribute('aria-busy', 'true');
      reportBtn.disabled = true;
      try {
        if (state.dirty) await savePoints();
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

  resetBtn.addEventListener('click', () => {
    state.points = state.originalPoints.map((p) => ({ ...p }));
    state.dirty = true;
    draw();
    toast('Точки сброшены к исходным', 'info');
  });

  /* --- Восстановление последнего фото при заходе --------------------------- */
  (async () => {
    await loadGender();
    try {
      const list = await api('/api/photos?limit=1');
      if (!list.items || !list.items.length) return;
      const detail = await api(`/api/photos/${list.items[0].id}`);
      state.photo = detail;
      state.points = ((detail.landmarks && detail.landmarks.points) || []).map(([x, y]) => ({ x, y }));
      state.originalPoints = state.points.map((p) => ({ ...p }));
      await loadImage(detail.image_url);
      renderQuality([]);
      runBtn.disabled = state.points.length === 0;
      resetBtn.disabled = state.points.length === 0;
      if (reportBtn) reportBtn.disabled = state.points.length === 0;
    } catch { /* гость или пусто — ничего не делаем */ }
  })();
})();

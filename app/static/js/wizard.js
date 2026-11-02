/* ==========================================================================
   FaceMetric — мастер ручной разметки лица (Wizard).

   Пошаговый режим: пользователь ставит 16 ключевых точек на фото, а полный
   меш из 106 точек считает бэкенд (RBF-деформация шаблона). Поверх фото
   рисуется полупрозрачный шаблон идеального лица с линиями третей, чтобы
   было видно, куда «сажать» лицо.

   Управление:
     * клик/тап по фото — поставить текущую точку;
     * колесо/кнопки — зум, перетаскивание — сдвиг кадра;
     * стрелки — точная подстройка (Shift — крупный шаг);
     * «Далее/Назад» — переход по шагам, экран сам зумится на нужную точку.

   Зависимостей нет: чистый JS и SVG, как требует проект (без Node/React).
   ========================================================================== */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const api = window.fmApi;
  const toast = window.fmToast;

  /** Цвета шагов: сгруппированы по смыслу, чтобы пользователь не путался. */
  const GROUP_COLORS = {
    left_pupil: '#4fd1c5', right_pupil: '#4fd1c5',
    left_eye_inner: '#7c9cff', left_eye_outer: '#7c9cff',
    right_eye_inner: '#7c9cff', right_eye_outer: '#7c9cff',
    nose_tip: '#f6c453', nose_left: '#f6c453', nose_right: '#f6c453',
    mouth_left: '#ff9f6b', mouth_right: '#ff9f6b',
    chin: '#ff6b6b',
    left_zygomatic: '#b78bff', right_zygomatic: '#b78bff',
    left_gonion: '#57d68d', right_gonion: '#57d68d',
  };

  const MIN_SCALE = 1;
  const MAX_SCALE = 6;
  const STEP_ZOOM = 2.4;

  let root = null;          // корневой узел модального окна
  let ctx = null;           // состояние сессии мастера
  let lastFocus = null;     // куда вернуть фокус при закрытии

  /* --- Модальное окно ------------------------------------------------------ */

  const buildModal = () => {
    const el = document.createElement('div');
    el.className = 'wizard';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-label', 'Мастер разметки лица');
    el.innerHTML = `
      <div class="wizard-backdrop" data-wiz-close></div>
      <div class="wizard-dialog">
        <header class="wizard-head">
          <h2 class="wizard-title">Разметка лица</h2>
          <button class="wizard-close" type="button" data-wiz-close aria-label="Закрыть мастер">✕</button>
        </header>

        <div class="wizard-body">
          <div class="wizard-stage" data-wiz-stage>
            <div class="wizard-content" data-wiz-content>
              <img class="wizard-image" data-wiz-image alt="Фото для разметки">
              <svg class="wizard-svg" data-wiz-svg xmlns="http://www.w3.org/2000/svg"></svg>
            </div>
            <div class="wizard-zoom">
              <button type="button" class="wizard-zoom-btn" data-wiz-zoom-in aria-label="Приблизить">＋</button>
              <button type="button" class="wizard-zoom-btn" data-wiz-zoom-out aria-label="Отдалить">－</button>
              <button type="button" class="wizard-zoom-btn" data-wiz-zoom-fit aria-label="По размеру">⤢</button>
            </div>
          </div>

          <aside class="wizard-panel">
            <div class="wizard-progress">
              <span data-wiz-counter>1 / 16</span>
              <div class="wizard-progress-track"><i data-wiz-progress></i></div>
            </div>

            <div class="wizard-step">
              <span class="wizard-dot" data-wiz-dot></span>
              <h3 data-wiz-label>Точка</h3>
              <p class="muted small" data-wiz-hint></p>
            </div>

            <div class="wizard-actions">
              <button class="btn btn-ghost" type="button" data-wiz-prev>Назад</button>
              <button class="btn btn-ghost" type="button" data-wiz-next>Далее</button>
            </div>
            <button class="btn btn-primary" type="button" data-wiz-save disabled>Сохранить разметку</button>

            <p class="hint small">
              Стрелки — сдвиг на 1 пиксель, Shift + стрелки — на 10.
              Точку можно поставить повторно, просто кликнув ещё раз.
            </p>
          </aside>
        </div>
      </div>`;
    return el;
  };

  /* --- Координаты ---------------------------------------------------------- */

  // Точки хранятся в нормализованных координатах 0..1. Экранные координаты
  // получаем через размеры отрисованного изображения, а не CSS-пиксели окна:
  // так разметка не зависит от масштаба и зума.
  const imageRect = () => {
    const img = $('[data-wiz-image]', root);
    return { rect: img.getBoundingClientRect(), nw: img.naturalWidth || 1, nh: img.naturalHeight || 1 };
  };

  const toNormalized = (clientX, clientY) => {
    const { rect } = imageRect();
    return {
      x: (clientX - rect.left) / rect.width,
      y: (clientY - rect.top) / rect.height,
    };
  };

  /* --- Отрисовка оверлея --------------------------------------------------- */

  const renderSvg = () => {
    const svg = $('[data-wiz-svg]', root);
    const img = $('[data-wiz-image]', root);
    const nw = img.naturalWidth || 1000;
    const nh = img.naturalHeight || 1000;
    svg.setAttribute('viewBox', `0 0 ${nw} ${nh}`);
    svg.setAttribute('preserveAspectRatio', 'none');

    const parts = [];

    // Шаблон идеального лица и линии третей: полупрозрачная подсказка, по
    // которой пользователь совмещает лицо. Не мешает кликам (pointer-events).
    const g = 'rgba(124,156,255,.42)';
    const dash = 'stroke-dasharray="14 12"';
    parts.push(`<g fill="none" stroke="${g}" stroke-width="${Math.max(1.5, nw / 500)}" ${dash}>`);
    // Вертикальные линии третей (по ширине): границы «пятых».
    for (let i = 1; i <= 4; i += 1) {
      const x = (nw * i) / 5;
      parts.push(`<line x1="${x}" y1="0" x2="${x}" y2="${nh}"/>`);
    }
    // Горизонтальные линии третей (по высоте).
    for (let i = 1; i <= 2; i += 1) {
      const y = (nh * i) / 3;
      parts.push(`<line x1="0" y1="${y}" x2="${nw}" y2="${y}"/>`);
    }
    // Овал «идеального лица» — мягкий ориентир для контура.
    parts.push(
      `<ellipse cx="${nw / 2}" cy="${nh * 0.52}" rx="${nw * 0.30}" ry="${nh * 0.42}"/>`,
    );
    // Линия симметрии.
    parts.push(`<line x1="${nw / 2}" y1="0" x2="${nw / 2}" y2="${nh}"/>`);
    parts.push('</g>');

    // Уже поставленные точки и текущая.
    ctx.anchors.forEach((anchor, index) => {
      const pos = ctx.points[anchor.key];
      if (!pos) return;
      const isCurrent = index === ctx.index;
      const color = GROUP_COLORS[anchor.key] || '#4fd1c5';
      const r = Math.max(4, nw / 160) * (isCurrent ? 1.6 : 1);
      parts.push(
        `<circle cx="${pos[0] * nw}" cy="${pos[1] * nh}" r="${r}" fill="${color}" ` +
          `stroke="${isCurrent ? '#ffffff' : 'rgba(0,0,0,.55)'}" ` +
          `stroke-width="${Math.max(1.5, nw / 400)}"/>`,
      );
      if (isCurrent) {
        const pr = r * 2.6;
        parts.push(
          `<circle cx="${pos[0] * nw}" cy="${pos[1] * nh}" r="${pr}" fill="none" ` +
            `stroke="${color}" stroke-width="${Math.max(1.5, nw / 380)}" opacity="0.85"/>`,
        );
      }
    });

    svg.innerHTML = parts.join('');
  };

  /* --- Зум и панорама ------------------------------------------------------ */

  const applyTransform = () => {
    const content = $('[data-wiz-content]', root);
    content.style.transform =
      `translate(${ctx.tx}px, ${ctx.ty}px) scale(${ctx.scale})`;
  };

  const clampPan = () => {
    const stage = $('[data-wiz-stage]', root);
    const content = $('[data-wiz-content]', root);
    const sw = stage.clientWidth;
    const sh = stage.clientHeight;
    const cw = content.offsetWidth * ctx.scale;
    const ch = content.offsetHeight * ctx.scale;
    // Если картинка меньше окна — центрируем; иначе не даём уехать за край.
    const maxX = Math.max(0, (cw - sw) / 2);
    const maxY = Math.max(0, (ch - sh) / 2);
    ctx.tx = Math.min(maxX, Math.max(-maxX, ctx.tx));
    ctx.ty = Math.min(maxY, Math.max(-maxY, ctx.ty));
  };

  const setScale = (scale) => {
    ctx.scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
    clampPan();
    applyTransform();
  };

  /** Зумится на конкретную точку — так шаг мастера виден крупно. */
  const focusOn = (point) => {
    if (!point) return;
    const stage = $('[data-wiz-stage]', root);
    const content = $('[data-wiz-content]', root);
    ctx.scale = STEP_ZOOM;
    const cw = content.offsetWidth;
    const ch = content.offsetHeight;
    // Смещаем так, чтобы точка оказалась в центре окна.
    ctx.tx = (0.5 - point[0]) * cw * ctx.scale;
    ctx.ty = (0.5 - point[1]) * ch * ctx.scale;
    clampPan();
    applyTransform();
  };

  const fitToStage = () => {
    ctx.scale = 1;
    ctx.tx = 0;
    ctx.ty = 0;
    applyTransform();
  };

  /* --- Состояние и шаги ---------------------------------------------------- */

  const updatePanel = () => {
    const anchor = ctx.anchors[ctx.index];
    const total = ctx.anchors.length;
    $('[data-wiz-counter]', root).textContent = `${ctx.index + 1} / ${total}`;
    $('[data-wiz-progress]', root).style.width = `${((ctx.index + 1) / total) * 100}%`;
    $('[data-wiz-label]', root).textContent = anchor.label;
    $('[data-wiz-hint]', root).textContent = anchor.hint;
    $('[data-wiz-dot]', root).style.background = GROUP_COLORS[anchor.key] || '#4fd1c5';

    const placed = ctx.anchors.filter((a) => ctx.points[a.key]).length;
    $('[data-wiz-save]', root).disabled = placed < total;
    renderSvg();
  };

  const goTo = (index, { zoom = true } = {}) => {
    ctx.index = Math.min(ctx.anchors.length - 1, Math.max(0, index));
    updatePanel();
    const anchor = ctx.anchors[ctx.index];
    if (zoom) focusOn(ctx.points[anchor.key]);
  };

  const placePoint = (x, y) => {
    const anchor = ctx.anchors[ctx.index];
    ctx.points[anchor.key] = [x, y];
    updatePanel();
    // Автопереход к следующей незаполненной точке — меньше кликов подряд.
    const nextEmpty = ctx.anchors.findIndex((a) => !ctx.points[a.key]);
    if (nextEmpty !== -1 && nextEmpty !== ctx.index) {
      setTimeout(() => goTo(nextEmpty), 160);
    }
  };

  /* --- События ------------------------------------------------------------- */

  const bindStage = () => {
    const stage = $('[data-wiz-stage]', root);
    let dragging = false;
    let moved = false;
    let startX = 0;
    let startY = 0;
    let startTx = 0;
    let startTy = 0;

    stage.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      dragging = true;
      moved = false;
      startX = e.clientX;
      startY = e.clientY;
      startTx = ctx.tx;
      startTy = ctx.ty;
      stage.setPointerCapture(e.pointerId);
    });

    stage.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) moved = true;
      ctx.tx = startTx + dx;
      ctx.ty = startTy + dy;
      clampPan();
      applyTransform();
    });

    stage.addEventListener('pointerup', (e) => {
      if (!dragging) return;
      dragging = false;
      // Короткое движение без сдвига — это клик: ставим точку.
      if (!moved) {
        const { x, y } = toNormalized(e.clientX, e.clientY);
        if (x >= 0 && x <= 1 && y >= 0 && y <= 1) placePoint(x, y);
      }
    });

    stage.addEventListener(
      'wheel',
      (e) => {
        e.preventDefault();
        setScale(ctx.scale * (e.deltaY < 0 ? 1.12 : 0.9));
      },
      { passive: false },
    );
  };

  const bindKeys = () => {
    const onKey = (e) => {
      if (!root || root.hidden) return;
      const anchor = ctx.anchors[ctx.index];
      const step = e.shiftKey ? 0.01 : 0.001;
      const pos = ctx.points[anchor.key];
      if (!pos) return;
      let dx = 0;
      let dy = 0;
      if (e.key === 'ArrowLeft') dx = -step;
      else if (e.key === 'ArrowRight') dx = step;
      else if (e.key === 'ArrowUp') dy = -step;
      else if (e.key === 'ArrowDown') dy = step;
      else return;
      e.preventDefault();
      ctx.points[anchor.key] = [
        Math.min(1, Math.max(0, pos[0] + dx)),
        Math.min(1, Math.max(0, pos[1] + dy)),
      ];
      renderSvg();
    };
    document.addEventListener('keydown', onKey);
    ctx.disposeKeys = () => document.removeEventListener('keydown', onKey);
  };

  const bindControls = () => {
    $('[data-wiz-zoom-in]', root).addEventListener('click', () => setScale(ctx.scale * 1.25));
    $('[data-wiz-zoom-out]', root).addEventListener('click', () => setScale(ctx.scale * 0.8));
    $('[data-wiz-zoom-fit]', root).addEventListener('click', fitToStage);
    $('[data-wiz-prev]', root).addEventListener('click', () => goTo(ctx.index - 1));
    $('[data-wiz-next]', root).addEventListener('click', () => goTo(ctx.index + 1));
    root.querySelectorAll('[data-wiz-close]').forEach((el) =>
      el.addEventListener('click', () => close()),
    );
    $('[data-wiz-save]', root).addEventListener('click', save);
  };

  /* --- Сохранение ---------------------------------------------------------- */

  const save = async () => {
    const btn = $('[data-wiz-save]', root);
    btn.disabled = true;
    btn.textContent = 'Сохраняем…';
    try {
      // 1) Считаем полный меш по ключевым точкам (на бэкенде, RBF).
      const mesh = await api(`/api/photos/${ctx.photoId}/wizard`, {
        method: 'POST',
        body: { anchors: ctx.points },
      });
      // 2) Сохраняем меш как landmarks фото — дальше работает обычный конвейер.
      await api(`/api/photos/${ctx.photoId}/landmarks`, {
        method: 'PUT',
        body: { points: mesh.points, provider: 'manual' },
      });
      toast('Разметка сохранена', 'success');
      const cb = ctx.onSaved;
      close();
      if (cb) cb(mesh);
    } catch (err) {
      toast(err.message || 'Не удалось сохранить разметку', 'error');
      btn.disabled = false;
      btn.textContent = 'Сохранить разметку';
    }
  };

  /* --- Открытие/закрытие --------------------------------------------------- */

  const close = () => {
    if (!root) return;
    if (ctx && ctx.disposeKeys) ctx.disposeKeys();
    root.remove();
    root = null;
    ctx = null;
    document.body.style.overflow = '';
    if (lastFocus) lastFocus.focus();
  };

  const open = async (photoId, { onSaved } = {}) => {
    if (!api) return;
    lastFocus = document.activeElement;
    try {
      const data = await api(`/api/photos/${photoId}/wizard`);
      root = buildModal();
      document.body.appendChild(root);
      document.body.style.overflow = 'hidden';

      // Стартовые позиции: автодетекция, иначе канонический шаблон.
      const points = {};
      data.anchors.forEach((a) => {
        if (a.auto) points[a.key] = [a.auto[0], a.auto[1]];
      });

      ctx = {
        photoId,
        anchors: data.anchors,
        points,
        index: 0,
        scale: 1,
        tx: 0,
        ty: 0,
        onSaved,
      };

      const img = $('[data-wiz-image]', root);
      img.addEventListener('load', () => {
        // Первый незаполненный шаг: не заставляем перепроверять автоточки.
        const firstEmpty = data.anchors.findIndex((a) => !points[a.key]);
        goTo(firstEmpty === -1 ? 0 : firstEmpty);
      });
      img.src = data.image_url;

      bindStage();
      bindControls();
      bindKeys();
      updatePanel();
    } catch (err) {
      if (root) { root.remove(); root = null; }
      document.body.style.overflow = '';
      if (toast) toast(err.message || 'Не удалось открыть мастер', 'error');
    }
  };

  window.fmWizard = { open, close };
})();

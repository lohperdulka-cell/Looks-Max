/* ==========================================================================
   FaceMetric — «Студия оценки»: загрузка фото, совмещение лица, пошаговая
   разметка и расчёт метрик.

   Почему всё в одном файле. Раньше логика была размазана по трём скриптам
   (rate.js, wizard.js, scan-anim.js) и делила состояние через глобальные
   объекты window.fmWizard / window.fmScan. Из-за этого мастер и страница
   расходились: канвас рисовал одни точки, мастер — другие. Здесь одна сессия
   оценки живёт в одном объекте state, а шаги — это три экрана одного мастера.

   Шаги:
     1. Выравнивание — фото под полупрозрачным шаблоном лица, панорама и зум.
     2. Разметка — 16 ключевых точек по одной, с автозумом и точной подстройкой.
     3. Результат — меш отрисовывается анимацией, затем чистый кадр и расчёт.

   Зависимостей нет: чистый JS + SVG, как требует проект (без Node/React).
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

  const reduceMotion = () =>
    window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* Цвета точек по смысловым группам: глаза, нос, рот, контур. */
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
  const MAX_SCALE = 8;
  const FOCUS_SCALE = 2.6;

  /* Нормализованные (0..1) ориентиры шаблона лица для шага 1. Значения — те же
     пропорции, что и в каноническом шаблоне меша, поэтому шаблон совпадает с
     тем, куда потом ложатся точки разметки. */
  const FACE_GUIDE = {
    eyes: [0.335, 0.44, 0.665, 0.44],
    nose: [[0.5, 0.44], [0.5, 0.60], [0.455, 0.615], [0.545, 0.615]],
    mouth: [0.43, 0.72, 0.57, 0.72],
    outline: [
      [0.5, 0.10], [0.665, 0.16], [0.72, 0.36], [0.715, 0.52], [0.70, 0.66],
      [0.665, 0.76], [0.60, 0.83], [0.5, 0.855],
      [0.40, 0.83], [0.335, 0.76], [0.30, 0.66], [0.285, 0.52], [0.28, 0.36],
      [0.335, 0.16],
    ],
  };

  /* -------------------------------------------------------------------------
     Студия
     ---------------------------------------------------------------------- */
  const studio = {
    root: null,
    photo: null,          // { id, image_url, ... }
    img: null,            // HTMLImageElement текущего фото
    anchors: [],          // шаги разметки с бэкенда
    points: {},           // key -> [x, y] нормализованные
    index: 0,
    step: 'align',
    // Панорама/зум шага 1 (в экранных пикселях, origin — центр).
    align: { scale: 1, tx: 0, ty: 0 },
    guideOn: true,
    lastFocus: null,
    disposers: [],
  };

  const stage = () => $('[data-studio-stage]', studio.root);

  /* --- Создание разметки модального окна ---------------------------------- */

  const buildModal = () => {
    const el = document.createElement('div');
    el.className = 'studio';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-label', 'Оценка лица по фото');
    el.innerHTML = `
      <header class="studio-head">
        <div class="studio-steps" role="list" aria-label="Этапы оценки">
          <span class="studio-step is-active" data-studio-stepdot="align" role="listitem">
            <b>1</b> Совмещение</span>
          <span class="studio-step" data-studio-stepdot="mark" role="listitem">
            <b>2</b> Разметка</span>
          <span class="studio-step" data-studio-stepdot="done" role="listitem">
            <b>3</b> Результат</span>
        </div>
        <button class="studio-close" type="button" data-studio-close aria-label="Закрыть">✕</button>
      </header>

      <div class="studio-stage" data-studio-stage>
        <div class="studio-canvas" data-studio-canvas>
          <img class="studio-photo" data-studio-photo alt="Фото для оценки">
          <svg class="studio-overlay" data-studio-overlay xmlns="http://www.w3.org/2000/svg"></svg>
        </div>
        <div class="studio-zoombar">
          <button type="button" class="studio-zbtn" data-studio-zoom-in aria-label="Приблизить">＋</button>
          <button type="button" class="studio-zbtn" data-studio-zoom-out aria-label="Отдалить">－</button>
          <button type="button" class="studio-zbtn" data-studio-zoom-fit aria-label="Вписать">⤢</button>
        </div>
      </div>

      <footer class="studio-foot" data-studio-foot></footer>`;
    return el;
  };

  /* --- Вспомогательное ---------------------------------------------------- */

  // Экранный прямоугольник изображения с учётом текущего зума: по нему считаем
  // нормализованные координаты, поэтому они не зависят от масштаба.
  const photoRect = () => studio.img.getBoundingClientRect();

  const toNormalized = (clientX, clientY) => {
    const r = photoRect();
    return { x: (clientX - r.left) / r.width, y: (clientY - r.top) / r.height };
  };

  const applyTransform = () => {
    const canvas = $('[data-studio-canvas]', studio.root);
    const { scale, tx, ty } = studio.align;
    canvas.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
    canvas.classList.toggle('is-zoomed', scale > 1.02);
  };

  const clampPan = () => {
    const st = stage();
    const img = studio.img;
    if (!st || !img) return;
    const sw = st.clientWidth;
    const sh = st.clientHeight;
    const iw = img.offsetWidth * studio.align.scale;
    const ih = img.offsetHeight * studio.align.scale;
    const maxX = Math.max(0, (iw - sw) / 2);
    const maxY = Math.max(0, (ih - sh) / 2);
    studio.align.tx = Math.min(maxX, Math.max(-maxX, studio.align.tx));
    studio.align.ty = Math.min(maxY, Math.max(-maxY, studio.align.ty));
  };

  const setScale = (scale) => {
    studio.align.scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
    clampPan();
    applyTransform();
  };

  const resetView = () => {
    studio.align.scale = 1;
    studio.align.tx = 0;
    studio.align.ty = 0;
    applyTransform();
  };

  /* --- Оверлей: шаблон лица (шаг 1) и точки (шаг 2) ----------------------- */

  const renderOverlay = () => {
    const svg = $('[data-studio-overlay]', studio.root);
    const img = studio.img;
    if (!svg || !img) return;
    const nw = img.naturalWidth || 1000;
    const nh = img.naturalHeight || 1000;
    svg.setAttribute('viewBox', `0 0 ${nw} ${nh}`);
    svg.setAttribute('preserveAspectRatio', 'none');

    const parts = [];

    if (studio.step === 'align' && studio.guideOn) {
      const stroke = Math.max(1.6, nw / 460);
      const g = 'rgba(124,156,255,.85)';
      const soft = 'rgba(124,156,255,.34)';
      const [lx, ly, rx, ry] = FACE_GUIDE.eyes;
      // Глаза: линия и две перекрестины.
      parts.push(
        `<line x1="${lx * nw}" y1="${ly * nh}" x2="${rx * nw}" y2="${ry * nh}" `
          + `stroke="${g}" stroke-width="${stroke}" stroke-dasharray="${stroke * 4} ${stroke * 3}"/>`,
      );
      [[lx, ly], [rx, ry]].forEach(([x, y]) => {
        const cx = x * nw;
        const cy = y * nh;
        const t = stroke * 5;
        parts.push(
          `<line x1="${cx - t}" y1="${cy}" x2="${cx + t}" y2="${cy}" stroke="${g}" stroke-width="${stroke}"/>`,
          `<line x1="${cx}" y1="${cy - t}" x2="${cx}" y2="${cy + t}" stroke="${g}" stroke-width="${stroke}"/>`,
        );
      });
      // Нос и рот — простые ориентиры.
      parts.push(
        `<polyline points="${FACE_GUIDE.nose.map(([x, y]) => `${x * nw},${y * nh}`).join(' ')}" `
          + `fill="none" stroke="${g}" stroke-width="${stroke}"/>`,
        `<line x1="${FACE_GUIDE.mouth[0] * nw}" y1="${FACE_GUIDE.mouth[1] * nh}" `
          + `x2="${FACE_GUIDE.mouth[2] * nw}" y2="${FACE_GUIDE.mouth[3] * nh}" `
          + `stroke="${g}" stroke-width="${stroke}"/>`,
      );
      // Линии третей.
      parts.push(
        `<g stroke="${soft}" stroke-width="${stroke}" stroke-dasharray="${stroke * 3} ${stroke * 4}">`
          + [1, 2, 3, 4].map((i) => `<line x1="${(nw * i) / 5}" y1="0" x2="${(nw * i) / 5}" y2="${nh}"/>`).join('')
          + [1, 2].map((i) => `<line x1="0" y1="${(nh * i) / 3}" x2="${nw}" y2="${(nh * i) / 3}"/>`).join('')
          + '</g>',
      );
      // Овал лица.
      parts.push(
        `<path d="${outlinePath()}" fill="none" stroke="${g}" stroke-width="${stroke * 1.6}" `
          + `stroke-linejoin="round" opacity="0.9"/>`,
      );
    }

    if (studio.step === 'mark' || studio.step === 'done') {
      const r = Math.max(3.2, nw / 210);
      studio.anchors.forEach((a) => {
        const p = studio.points[a.key];
        if (!p) return;
        const color = GROUP_COLORS[a.key] || '#4fd1c5';
        parts.push(
          `<circle cx="${p[0] * nw}" cy="${p[1] * nh}" r="${r}" fill="${color}" `
            + `stroke="#ffffff" stroke-width="${Math.max(1, nw / 700)}"/>`,
        );
      });
    }

    svg.innerHTML = parts.join('');
  };

  /** Гладкий контур лица: точки шаблона соединяем кубическими кривыми. */
  const outlinePath = () => {
    const pts = FACE_GUIDE.outline;
    const to = (p) => `${p[0] * 1000},${p[1] * 1000}`;
    let d = `M ${to(pts[0])}`;
    for (let i = 1; i < pts.length; i += 1) {
      const prev = pts[i - 1];
      const cur = pts[i];
      const cx = ((prev[0] + cur[0]) / 2) * 1000;
      const cy = ((prev[1] + cur[1]) / 2) * 1000;
      d += ` Q ${to(prev)} ${cx},${cy}`;
    }
    const first = pts[0];
    const last = pts[pts.length - 1];
    d += ` Q ${to(last)} ${((first[0] + last[0]) / 2) * 1000},${((first[1] + last[1]) / 2) * 1000} Z`;
    return d;
  };

  /* --- Панель под сценой --------------------------------------------------- */

  const renderFoot = () => {
    const foot = $('[data-studio-foot]', studio.root);
    if (!foot) return;

    if (studio.step === 'align') {
      foot.innerHTML = `
        <p class="studio-hint">
          Совместите лицо с шаблоном: двигайте фото пальцем или мышью,
          масштаб — щипком или колесом. Глаза должны лечь на голубую линию.
        </p>
        <div class="studio-actions">
          <button class="btn btn-ghost btn-sm" type="button" data-studio-guide>
            ${studio.guideOn ? 'Скрыть шаблон' : 'Показать шаблон'}
          </button>
          <button class="btn btn-primary" type="button" data-studio-next>
            Лицо совмещено, далее
          </button>
        </div>`;
      return;
    }

    if (studio.step === 'mark') {
      const a = studio.anchors[studio.index];
      const total = studio.anchors.length;
      const placed = Boolean(studio.points[a.key]);
      foot.innerHTML = `
        <div class="studio-progress">
          <div class="studio-progress-track"><i style="width:${((studio.index + 1) / total) * 100}%"></i></div>
          <span class="studio-progress-count">${studio.index + 1} / ${total}</span>
        </div>
        <p class="studio-hint">
          <span class="studio-dot" style="background:${GROUP_COLORS[a.key] || '#4fd1c5'}"></span>
          <strong>${escapeHtml(a.label)}.</strong> ${escapeHtml(a.hint)}
          ${placed ? '<em>Точка уже стоит — поправьте её или сразу зафиксируйте.</em>' : ''}
        </p>
        <div class="studio-nudge" role="group" aria-label="Точный сдвиг точки">
          <button type="button" class="studio-nudge-btn" data-studio-nudge="up" aria-label="Сдвинуть вверх">▲</button>
          <div class="studio-nudge-row">
            <button type="button" class="studio-nudge-btn" data-studio-nudge="left" aria-label="Сдвинуть влево">◀</button>
            <button type="button" class="studio-nudge-btn" data-studio-nudge="down" aria-label="Сдвинуть вниз">▼</button>
            <button type="button" class="studio-nudge-btn" data-studio-nudge="right" aria-label="Сдвинуть вправо">▶</button>
          </div>
        </div>
        <div class="studio-actions">
          <button class="btn btn-ghost btn-sm" type="button" data-studio-prev
                  ${studio.index === 0 ? 'disabled' : ''}>Назад</button>
          <button class="btn btn-primary" type="button" data-studio-fix>
            Зафиксировать и далее
          </button>
        </div>`;
      const fix = $('[data-studio-fix]', foot);
      if (studio.index === total - 1) fix.textContent = 'Зафиксировать и завершить';
      return;
    }

    foot.innerHTML = `
      <p class="studio-hint" data-studio-done-hint>
        Разметка готова. Метрики считаются по пропорциям лица, а не по внешности.
      </p>
      <div class="studio-actions">
        <button class="btn btn-ghost btn-sm" type="button" data-studio-back-edit>
          Поправить точки
        </button>
        <button class="btn btn-primary" type="button" data-studio-calc>
          Рассчитать метрики
        </button>
      </div>`;
  };

  const setStep = (step) => {
    studio.step = step;
    $$('[data-studio-stepdot]', studio.root).forEach((el) => {
      const order = ['align', 'mark', 'done'];
      el.classList.toggle('is-active', el.dataset.studioStepdot === step);
      el.classList.toggle(
        'is-done',
        order.indexOf(el.dataset.studioStepdot) < order.indexOf(step),
      );
    });
    // Зум нужен только там, где пользователь сам двигает кадр.
    const zoombar = $('.studio-zoombar', studio.root);
    if (zoombar) zoombar.hidden = step === 'done';
    const mesh = $('[data-studio-mesh]', studio.root);
    if (mesh) mesh.hidden = step !== 'done';
    renderFoot();
    renderOverlay();
  };

  /* --- Шаг 1: панорама и зум ---------------------------------------------- */

  const bindAlign = () => {
    const st = stage();
    const pointers = new Map();
    let startDist = 0;
    let startScale = 1;
    let startMid = null;
    let startTx = 0;
    let startTy = 0;
    let dragging = false;

    const onDown = (e) => {
      if (studio.step !== 'align') return;
      // Кнопки внутри сцены обрабатывают клик сами: захват указателя сцены
      // перенаправил бы click на сцену, и кнопка не сработала бы.
      if (e.target.closest('button')) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      st.setPointerCapture(e.pointerId);
      if (pointers.size === 1) {
        dragging = true;
        startMid = { x: e.clientX, y: e.clientY };
        startTx = studio.align.tx;
        startTy = studio.align.ty;
      } else if (pointers.size === 2) {
        const [a, b] = [...pointers.values()];
        startDist = Math.hypot(a.x - b.x, a.y - b.y);
        startScale = studio.align.scale;
        dragging = false;
      }
    };

    const onMove = (e) => {
      if (studio.step !== 'align' || !pointers.has(e.pointerId)) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      e.preventDefault();

      if (pointers.size >= 2) {
        const [a, b] = [...pointers.values()];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        if (startDist > 0) setScale(startScale * (dist / startDist));
        return;
      }
      if (dragging && startMid) {
        studio.align.tx = startTx + (e.clientX - startMid.x);
        studio.align.ty = startTy + (e.clientY - startMid.y);
        clampPan();
        applyTransform();
      }
    };

    const onUp = (e) => {
      pointers.delete(e.pointerId);
      if (pointers.size < 2) startDist = 0;
      if (pointers.size === 0) dragging = false;
    };

    st.addEventListener('pointerdown', onDown);
    st.addEventListener('pointermove', onMove);
    st.addEventListener('pointerup', onUp);
    st.addEventListener('pointercancel', onUp);
    st.addEventListener('wheel', (e) => {
      if (studio.step !== 'align') return;
      e.preventDefault();
      setScale(studio.align.scale * (e.deltaY < 0 ? 1.12 : 0.9));
    }, { passive: false });
  };

  /* --- Шаг 2: постановка точек -------------------------------------------- */

  /** Плавно зумится на область текущей точки, чтобы шаг был виден крупно. */
  const focusOnPoint = (p) => {
    if (!p) return;
    const img = studio.img;
    studio.align.scale = FOCUS_SCALE;
    // Смещаем так, чтобы точка оказалась в центре окна.
    studio.align.tx = (0.5 - p[0]) * img.offsetWidth * FOCUS_SCALE;
    studio.align.ty = (0.5 - p[1]) * img.offsetHeight * FOCUS_SCALE;
    clampPan();
    applyTransform();
  };

  const goTo = (index, { zoom = true } = {}) => {
    studio.index = Math.min(studio.anchors.length - 1, Math.max(0, index));
    renderFoot();
    renderOverlay();
    const a = studio.anchors[studio.index];
    // Если точка ещё не поставлена, зумимся к каноническому ориентиру: после
    // совмещения лицо примерно там, и шаг всё равно виден крупно.
    if (zoom) focusOnPoint(studio.points[a.key] || a.canonical);
  };

  const placePoint = (x, y) => {
    const a = studio.anchors[studio.index];
    studio.points[a.key] = [x, y];
    renderOverlay();
    renderFoot();
  };

  const nudge = (dir) => {
    const a = studio.anchors[studio.index];
    const p = studio.points[a.key] || [a.auto ? a.auto[0] : a.canonical[0], a.auto ? a.auto[1] : a.canonical[1]];
    // Шаг — доля кадра. 0.004 при ширине 500 px даёт ~2 px: мелко, но заметно.
    const step = 0.004;
    if (dir === 'left') p[0] -= step;
    else if (dir === 'right') p[0] += step;
    else if (dir === 'up') p[1] -= step;
    else if (dir === 'down') p[1] += step;
    studio.points[a.key] = [Math.min(1, Math.max(0, p[0])), Math.min(1, Math.max(0, p[1]))];
    renderOverlay();
  };

  const bindMark = () => {
    const st = stage();
    let dragging = false;
    let moved = false;
    let startX = 0;
    let startY = 0;
    let startTx = 0;
    let startTy = 0;

    st.addEventListener('pointerdown', (e) => {
      if (studio.step !== 'mark' || e.button !== 0) return;
      if (e.target.closest('button')) return;
      dragging = true;
      moved = false;
      startX = e.clientX;
      startY = e.clientY;
      startTx = studio.align.tx;
      startTy = studio.align.ty;
      st.setPointerCapture(e.pointerId);
    });

    st.addEventListener('pointermove', (e) => {
      if (!dragging || studio.step !== 'mark') return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      if (Math.abs(dx) > 5 || Math.abs(dy) > 5) moved = true;
      studio.align.tx = startTx + dx;
      studio.align.ty = startTy + dy;
      clampPan();
      applyTransform();
    });

    st.addEventListener('pointerup', (e) => {
      if (!dragging || studio.step !== 'mark') return;
      dragging = false;
      if (moved) return;
      // Короткое касание без сдвига — ставим точку.
      const { x, y } = toNormalized(e.clientX, e.clientY);
      if (x >= 0 && x <= 1 && y >= 0 && y <= 1) placePoint(x, y);
    });
  };

  const bindKeys = () => {
    const onKey = (e) => {
      if (!studio.root || studio.step !== 'mark') return;
      const map = {
        ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down',
      };
      if (map[e.key]) {
        e.preventDefault();
        nudge(map[e.key]);
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        fixCurrent();
      }
    };
    document.addEventListener('keydown', onKey);
    studio.disposers.push(() => document.removeEventListener('keydown', onKey));
  };

  const fixCurrent = () => {
    const a = studio.anchors[studio.index];
    if (!studio.points[a.key]) {
      toast('Сначала поставьте точку на фото', 'warn');
      return;
    }
    if (studio.index === studio.anchors.length - 1) {
      finishMarking();
      return;
    }
    const nextEmpty = studio.anchors.findIndex((x, i) => i > studio.index && !studio.points[x.key]);
    goTo(nextEmpty === -1 ? studio.index + 1 : nextEmpty);
  };

  /* --- Шаг 3: отрисовка меша и расчёт ------------------------------------- */

  /** Канвас для меша: один на сессию, пересоздаётся, если его убрали. */
  const ensureMeshCanvas = () => {
    let canvas = $('[data-studio-mesh]', studio.root);
    if (canvas) return canvas;
    canvas = document.createElement('canvas');
    canvas.className = 'studio-mesh';
    canvas.setAttribute('data-studio-mesh', '');
    $('[data-studio-canvas]', studio.root).appendChild(canvas);
    return canvas;
  };

  /** Анимация «сканирования»: от ключевых точек прорастает полный меш. */
  const playMeshAnimation = (mesh) => new Promise((resolve) => {
    const canvas = ensureMeshCanvas();
    const img = studio.img;
    if (!img) { resolve(); return; }
    const W = (canvas.width = img.naturalWidth || 800);
    const H = (canvas.height = img.naturalHeight || 800);
    const c = canvas.getContext('2d');
    const key = Object.values(studio.points);
    const target = mesh.points.map(([x, y]) => [x * W, y * H]);
    const seeds = key.map(([x, y]) => [x * W, y * H]);

    const drawFrame = (t) => {
      c.clearRect(0, 0, W, H);
      const radius = t * Math.hypot(W, H) * 0.75;
      const shown = [];
      target.forEach((p, i) => {
        // Точка проявляется, когда «волна» от ближайшей ключевой точки дошла.
        const s = seeds[i % Math.max(1, seeds.length)] || [W / 2, H / 2];
        const dist = Math.hypot(p[0] - s[0], p[1] - s[1]);
        if (dist <= radius) shown.push(p);
      });
      c.fillStyle = 'rgba(79,209,197,.92)';
      shown.forEach(([x, y]) => {
        c.beginPath();
        c.arc(x, y, Math.max(1, W / 420), 0, Math.PI * 2);
        c.fill();
      });
      // Паутинка: соединяем близкие проявившиеся точки.
      c.strokeStyle = `rgba(124,156,255,${0.30 * t})`;
      c.lineWidth = Math.max(0.6, W / 900);
      for (let i = 0; i < shown.length; i += 1) {
        for (let j = i + 1; j < shown.length; j += 1) {
          const d = Math.hypot(shown[i][0] - shown[j][0], shown[i][1] - shown[j][1]);
          if (d < W * 0.11) {
            c.beginPath();
            c.moveTo(shown[i][0], shown[i][1]);
            c.lineTo(shown[j][0], shown[j][1]);
            c.stroke();
          }
        }
      }
      // Ключевые точки — ярче.
      c.fillStyle = '#ffffff';
      seeds.forEach(([x, y]) => {
        c.beginPath();
        c.arc(x, y, Math.max(1.6, W / 300), 0, Math.PI * 2);
        c.fill();
      });
    };

    if (reduceMotion()) {
      drawFrame(1);
      setTimeout(resolve, 300);
      return;
    }
    const dur = 1500;
    const t0 = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - t0) / dur);
      drawFrame(1 - Math.pow(1 - t, 3));
      if (t < 1) requestAnimationFrame(tick);
      else setTimeout(resolve, 350);
    };
    requestAnimationFrame(tick);
  });

  const finishMarking = async () => {
    setStep('done');
    renderOverlay();
    const btn = $('[data-studio-calc]', studio.root);
    if (btn) btn.disabled = true;
    try {
      const mesh = await api(`/api/photos/${studio.photo.id}/wizard`, {
        method: 'POST',
        body: { anchors: studio.points },
      });
      studio.mesh = mesh;
      await playMeshAnimation(mesh);
      // Точки и меш гаснут — остаётся чистый кадр, как и просил заказчик.
      $('[data-studio-overlay]', studio.root).innerHTML = '';
      const meshCanvas = $('[data-studio-mesh]', studio.root);
      if (meshCanvas) meshCanvas.remove();
      const hint = $('[data-studio-done-hint]', studio.root);
      if (hint) {
        hint.textContent =
          'Меш построен. Нажмите «Рассчитать метрики» — расчёт идёт по пропорциям лица.';
      }
      if (btn) btn.disabled = false;
    } catch (err) {
      toast(err.message || 'Не удалось построить разметку', 'error');
      setStep('mark');
      goTo(studio.index, { zoom: false });
    }
  };

  const calculate = async () => {
    const btn = $('[data-studio-calc]', studio.root);
    if (!btn || !studio.mesh) return;
    btn.disabled = true;
    btn.setAttribute('aria-busy', 'true');
    try {
      // Сохраняем восстановленный меш как landmarks фото — дальше работает
      // обычный конвейер: предпросмотр и отчёт читают те же точки.
      await api(`/api/photos/${studio.photo.id}/landmarks`, {
        method: 'PUT',
        body: { points: studio.mesh.points, provider: 'manual' },
      });
      const onDone = studio.onDone;
      close();
      if (onDone) onDone(studio.mesh);
    } catch (err) {
      toast(err.message || 'Не удалось сохранить разметку', 'error');
      btn.disabled = false;
      btn.removeAttribute('aria-busy');
    }
  };

  /* --- События интерфейса -------------------------------------------------- */

  const bindFoot = () => {
    const foot = $('[data-studio-foot]', studio.root);
    foot.addEventListener('click', (e) => {
      const t = e.target.closest('button');
      if (!t) return;
      if (t.hasAttribute('data-studio-guide')) {
        studio.guideOn = !studio.guideOn;
        renderFoot();
        renderOverlay();
      } else if (t.hasAttribute('data-studio-next')) {
        setStep('mark');
        const firstEmpty = studio.anchors.findIndex((a) => !studio.points[a.key]);
        goTo(firstEmpty === -1 ? 0 : firstEmpty);
      } else if (t.hasAttribute('data-studio-prev')) {
        goTo(studio.index - 1);
      } else if (t.hasAttribute('data-studio-fix')) {
        fixCurrent();
      } else if (t.hasAttribute('data-studio-back-edit')) {
        setStep('mark');
        goTo(studio.index, { zoom: false });
      } else if (t.hasAttribute('data-studio-calc')) {
        calculate();
      } else {
        const n = t.getAttribute('data-studio-nudge');
        if (n) nudge(n);
      }
    });
  };

  const bindZoomButtons = () => {
    $('[data-studio-zoom-in]', studio.root).addEventListener('click', () => setScale(studio.align.scale * 1.25));
    $('[data-studio-zoom-out]', studio.root).addEventListener('click', () => setScale(studio.align.scale * 0.8));
    $('[data-studio-zoom-fit]', studio.root).addEventListener('click', resetView);
  };

  /* --- Открытие/закрытие --------------------------------------------------- */

  const close = () => {
    if (!studio.root) return;
    studio.disposers.forEach((fn) => fn());
    studio.disposers = [];
    studio.root.remove();
    studio.root = null;
    studio.photo = null;
    studio.img = null;
    document.body.style.overflow = '';
    if (studio.lastFocus) studio.lastFocus.focus();
  };

  const open = async (photoId, { onDone } = {}) => {
    studio.lastFocus = document.activeElement;
    try {
      const data = await api(`/api/photos/${photoId}/wizard`);
      studio.photo = { id: photoId, image_url: data.image_url };
      studio.anchors = data.anchors;
      studio.points = {};
      data.anchors.forEach((a) => {
        if (a.auto) studio.points[a.key] = [a.auto[0], a.auto[1]];
      });
      studio.index = 0;
      studio.onDone = onDone;
      studio.guideOn = true;

      studio.root = buildModal();
      document.body.appendChild(studio.root);
      document.body.style.overflow = 'hidden';

      const img = $('[data-studio-photo]', studio.root);
      studio.img = img;
      ensureMeshCanvas().hidden = true;

      await new Promise((resolve, reject) => {
        img.onload = resolve;
        img.onerror = () => reject(new Error('Не удалось показать фото'));
        img.src = data.image_url;
      });

      bindAlign();
      bindMark();
      bindKeys();
      bindFoot();
      bindZoomButtons();
      $('[data-studio-close]', studio.root).addEventListener('click', close);
      const onEscape = (e) => {
        if (e.key === 'Escape') close();
      };
      document.addEventListener('keydown', onEscape);
      studio.disposers.push(() => document.removeEventListener('keydown', onEscape));

      setStep('align');
      resetView();
    } catch (err) {
      if (studio.root) { studio.root.remove(); studio.root = null; }
      document.body.style.overflow = '';
      if (toast) toast(err.message || 'Не удалось открыть студию', 'error');
    }
  };

  window.fmStudio = { open, close };
})();

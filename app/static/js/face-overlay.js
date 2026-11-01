/* ==========================================================================
   FaceMetric — отрисовка геометрии метрик поверх фото.
   Общий модуль для страницы оценки (анимация) и отчёта (галерея метрик).
   Схема точек определяется их числом: 478 (MediaPipe), 106 (InsightFace),
   68 (dlib-подобная). Индексы совпадают с app/ml/metrics.py.
   ========================================================================== */
(() => {
  'use strict';

  const MP = {
    leftEyeInner: 133, leftEyeOuter: 33,
    rightEyeInner: 362, rightEyeOuter: 263,
    leftBrowInner: 107, leftBrowOuter: 46,
    rightBrowInner: 336, rightBrowOuter: 276,
    noseTip: 1, noseLeft: 98, noseRight: 327,
    mouthLeft: 61, mouthRight: 291,
    upperLip: 13, lowerLip: 14,
    chin: 152, foreheadTop: 10,
    jawLeft: 172, jawRight: 397,
    zygoLeft: 234, zygoRight: 454,
  };

  const IF = {
    leftEyeInner: 35, leftEyeOuter: 33,
    rightEyeInner: 89, rightEyeOuter: 87,
    leftBrowInner: 43, leftBrowOuter: 46,
    rightBrowInner: 97, rightBrowOuter: 100,
    noseTip: 86, noseLeft: 74, noseRight: 98,
    mouthLeft: 60, mouthRight: 105,
    upperLip: 62, lowerLip: 66,
    chin: 16, foreheadTop: 27,
    jawLeft: 1, jawRight: 15,
    zygoLeft: 2, zygoRight: 14,
  };

  const LEGACY = {
    leftEyeInner: 39, leftEyeOuter: 36,
    rightEyeInner: 42, rightEyeOuter: 45,
    leftBrowInner: 21, leftBrowOuter: 17,
    rightBrowInner: 22, rightBrowOuter: 26,
    noseTip: 30, noseLeft: 31, noseRight: 35,
    mouthLeft: 48, mouthRight: 54,
    upperLip: 51, lowerLip: 57,
    chin: 8, foreheadTop: 27,
    jawLeft: 0, jawRight: 16,
    zygoLeft: 1, zygoRight: 15,
  };

  const MIRROR_PAIRS = {
    mp: [[33, 263], [133, 362], [159, 386], [145, 374], [46, 276], [53, 283],
      [70, 300], [63, 293], [61, 291], [98, 327], [172, 397], [234, 454],
      [132, 361], [58, 288], [136, 365], [150, 379]],
    if: [[33, 87], [35, 89], [43, 97], [46, 100], [60, 105], [74, 98],
      [1, 15], [2, 14], [52, 104], [55, 102], [56, 103]],
    legacy: [[36, 45], [39, 42], [31, 35], [48, 54], [2, 14], [0, 16]],
  };

  const indexMap = (n) => (n >= 468 ? MP : n >= 106 ? IF : LEGACY);
  const mirrorPairs = (n) => (n >= 468 ? MIRROR_PAIRS.mp : n >= 106 ? MIRROR_PAIRS.if : MIRROR_PAIRS.legacy);

  const PALETTE = {
    line: '#4fd1c5',
    lineSoft: 'rgba(79, 209, 197, .35)',
    accent: '#7c9cff',
    warn: '#f6c453',
    danger: '#ff6b6b',
    dot: '#4fd1c5',
    halo: 'rgba(11, 15, 20, .85)',
  };

  const mid = (a, b) => [((a[0] + b[0]) / 2), ((a[1] + b[1]) / 2)];

  /** Рисует «меш» из точек, соединяя заданные пары индексов. */
  const drawMesh = (ctx, pts, pairs, style) => {
    ctx.save();
    ctx.strokeStyle = style.color || PALETTE.lineSoft;
    ctx.lineWidth = style.width || 1;
    if (style.dash) ctx.setLineDash(style.dash);
    ctx.beginPath();
    pairs.forEach(([i, j]) => {
      if (i >= pts.length || j >= pts.length) return;
      ctx.moveTo(pts[i][0], pts[i][1]);
      ctx.lineTo(pts[j][0], pts[j][1]);
    });
    ctx.stroke();
    ctx.restore();
  };

  const dot = (ctx, p, r, fill) => {
    ctx.beginPath();
    ctx.arc(p[0], p[1], r || 3.5, 0, Math.PI * 2);
    ctx.fillStyle = fill || PALETTE.dot;
    ctx.fill();
    ctx.lineWidth = 1.4;
    ctx.strokeStyle = PALETTE.halo;
    ctx.stroke();
  };

  const line = (ctx, a, b, style) => {
    ctx.save();
    ctx.strokeStyle = style.color || PALETTE.line;
    ctx.lineWidth = style.width || 2;
    if (style.dash) ctx.setLineDash(style.dash);
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
    ctx.restore();
  };

  const angleArc = (ctx, vertex, a, b, radius, color) => {
    const a1 = Math.atan2(a[1] - vertex[1], a[0] - vertex[0]);
    const a2 = Math.atan2(b[1] - vertex[1], b[0] - vertex[0]);
    let start = a1;
    let end = a2;
    let diff = end - start;
    while (diff > Math.PI) diff -= Math.PI * 2;
    while (diff < -Math.PI) diff += Math.PI * 2;
    ctx.save();
    ctx.strokeStyle = color || PALETTE.warn;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(vertex[0], vertex[1], radius, start, start + diff, diff < 0);
    ctx.stroke();
    ctx.restore();
  };

  const label = (ctx, text, at, color) => {
    ctx.save();
    ctx.font = '600 12px ui-monospace, Menlo, monospace';
    const w = ctx.measureText(text).width;
    const x = Math.min(Math.max(at[0], w / 2 + 4), ctx.canvas.width - w / 2 - 4);
    ctx.fillStyle = 'rgba(11, 15, 20, .78)';
    ctx.beginPath();
    const rx = x - w / 2 - 6;
    const ry = at[1] - 9;
    ctx.roundRect ? ctx.roundRect(rx, ry, w + 12, 18, 6) : ctx.rect(rx, ry, w + 12, 18);
    ctx.fill();
    ctx.fillStyle = color || '#e8eef6';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x, at[1]);
    ctx.restore();
  };

  const angleBetween = (vertex, a, b) => {
    const v1 = [a[0] - vertex[0], a[1] - vertex[1]];
    const v2 = [b[0] - vertex[0], b[1] - vertex[1]];
    const dotv = v1[0] * v2[0] + v1[1] * v2[1];
    const n1 = Math.hypot(v1[0], v1[1]);
    const n2 = Math.hypot(v2[0], v2[1]);
    if (n1 < 1e-6 || n2 < 1e-6) return 0;
    return (Math.acos(Math.max(-1, Math.min(1, dotv / (n1 * n2)))) * 180) / Math.PI;
  };

  const tiltDeg = (inner, outer) => {
    const dy = outer[1] - inner[1];
    const dx = Math.abs(outer[0] - inner[0]);
    if (dx < 1e-6) return 0;
    return (Math.atan2(-dy, dx) * 180) / Math.PI;
  };

  /**
   * Рисует геометрию конкретной метрики.
   * @param {CanvasRenderingContext2D} ctx
   * @param {Array<[number,number]>} ptsPx — точки в пикселях канваса
   * @param {string} key — ключ метрики
   * @param {object} opts — { color, showLabels }
   */
  const drawMetric = (ctx, ptsPx, key, opts = {}) => {
    const m = indexMap(ptsPx.length);
    const P = (name) => ptsPx[m[name]];
    const has = (name) => m[name] !== undefined && m[name] < ptsPx.length;
    const color = opts.color || PALETTE.line;
    const showLabels = opts.showLabels !== false;
    const W = ctx.canvas.width;

    const safe = (name) => (has(name) ? P(name) : null);

    switch (key) {
      case 'facial_symmetry': {
        const top = safe('foreheadTop');
        const chin = safe('chin');
        if (top && chin) line(ctx, top, chin, { color: PALETTE.accent, dash: [6, 5] });
        mirrorPairs(ptsPx.length).forEach(([i, j]) => {
          if (i >= ptsPx.length || j >= ptsPx.length) return;
          const a = ptsPx[i];
          const b = ptsPx[j];
          line(ctx, a, b, { color: PALETTE.lineSoft, width: 1, dash: [4, 4] });
          dot(ctx, a, 3, PALETTE.line);
          dot(ctx, b, 3, PALETTE.line);
        });
        break;
      }
      case 'canthal_tilt': {
        [['leftEyeInner', 'leftEyeOuter'], ['rightEyeInner', 'rightEyeOuter']].forEach(([i, o]) => {
          const a = safe(i);
          const b = safe(o);
          if (!a || !b) return;
          line(ctx, a, b, { color, width: 2.5 });
          dot(ctx, a, 4);
          dot(ctx, b, 4);
          const deg = tiltDeg(a, b);
          if (showLabels) label(ctx, `${deg.toFixed(1)}°`, mid(a, b), color);
        });
        break;
      }
      case 'brow_tilt': {
        [['leftBrowInner', 'leftBrowOuter'], ['rightBrowInner', 'rightBrowOuter']].forEach(([i, o]) => {
          const a = safe(i);
          const b = safe(o);
          if (!a || !b) return;
          line(ctx, a, b, { color, width: 2.5 });
          dot(ctx, a, 4);
          dot(ctx, b, 4);
          const deg = tiltDeg(a, b);
          if (showLabels) label(ctx, `${deg.toFixed(1)}°`, mid(a, b), color);
        });
        break;
      }
      case 'facial_thirds': {
        const brow = has('leftBrowInner') && has('rightBrowInner')
          ? mid(P('leftBrowInner'), P('rightBrowInner')) : null;
        const rows = [
          ['foreheadTop', 'Верх'],
          [null, 'Брови'],
          ['noseTip', 'Нос'],
          ['chin', 'Подбородок'],
        ];
        let yBrow = brow ? brow[1] : null;
        rows.forEach(([name, text], k) => {
          let y = null;
          if (k === 1 && yBrow !== null) y = yBrow;
          else if (name && safe(name)) y = P(name)[1];
          if (y === null) return;
          line(ctx, [0, y], [W, y], { color: PALETTE.lineSoft, width: 1, dash: [5, 5] });
          if (showLabels) label(ctx, text, [W * 0.5, y], color);
        });
        break;
      }
      case 'facial_fifths': {
        const zl = safe('zygoLeft');
        const zr = safe('zygoRight');
        if (zl && zr) {
          const x1 = Math.min(zl[0], zr[0]);
          const x2 = Math.max(zl[0], zr[0]);
          for (let i = 0; i <= 5; i += 1) {
            const x = x1 + ((x2 - x1) * i) / 5;
            line(ctx, [x, 0], [x, ctx.canvas.height], { color: PALETTE.lineSoft, width: 1, dash: [5, 5] });
          }
          if (showLabels) label(ctx, 'пять зон', [(x1 + x2) / 2, ctx.canvas.height - 14], color);
        }
        break;
      }
      case 'golden_ratio': {
        const top = safe('foreheadTop');
        const chin = safe('chin');
        const nose = safe('noseTip');
        const mouth = has('upperLip') && has('lowerLip') ? mid(P('upperLip'), P('lowerLip')) : null;
        if (top && chin) {
          line(ctx, [top[0], top[1]], [chin[0], chin[1]], { color, width: 2 });
          [['foreheadTop', top], ['noseTip', nose], ['mouth', mouth], ['chin', chin]].forEach(([name, p]) => {
            if (!p) return;
            const x = has('zygoLeft') && has('zygoRight')
              ? (P('zygoLeft')[0] + P('zygoRight')[0]) / 2 : p[0];
            line(ctx, [x - 26, p[1]], [x + 26, p[1]], { color, width: 2 });
          });
          if (showLabels) label(ctx, '1 : 1.618', [top[0], (top[1] + chin[1]) / 2], color);
        }
        break;
      }
      case 'interpupillary_ratio': {
        const le = has('leftEyeInner') && has('leftEyeOuter') ? mid(P('leftEyeInner'), P('leftEyeOuter')) : null;
        const re = has('rightEyeInner') && has('rightEyeOuter') ? mid(P('rightEyeInner'), P('rightEyeOuter')) : null;
        const zl = safe('zygoLeft');
        const zr = safe('zygoRight');
        if (le && re) {
          line(ctx, le, re, { color, width: 2.5 });
          dot(ctx, le, 4.5);
          dot(ctx, re, 4.5);
          if (showLabels) label(ctx, 'межзрачковое', mid(le, re), color);
        }
        if (zl && zr) line(ctx, zl, zr, { color: PALETTE.lineSoft, width: 1.5, dash: [6, 4] });
        break;
      }
      case 'eye_spacing': {
        const li = safe('leftEyeInner');
        const ri = safe('rightEyeInner');
        const lo = safe('leftEyeOuter');
        const ro = safe('rightEyeOuter');
        if (li && ri) { line(ctx, li, ri, { color, width: 2.5 }); dot(ctx, li, 4); dot(ctx, ri, 4); }
        if (lo && ro) line(ctx, lo, ro, { color: PALETTE.lineSoft, width: 1.5, dash: [5, 4] });
        if (li && ri && showLabels) label(ctx, 'межглазье', mid(li, ri), color);
        break;
      }
      case 'nose_proportion': {
        const nl = safe('noseLeft');
        const nr = safe('noseRight');
        const tip = safe('noseTip');
        if (nl && nr) line(ctx, nl, nr, { color, width: 2.5 });
        if (tip) dot(ctx, tip, 4);
        if (nl && nr && tip && showLabels) label(ctx, 'ширина носа', mid(nl, nr), color);
        break;
      }
      case 'mouth_proportion': {
        const ml = safe('mouthLeft');
        const mr = safe('mouthRight');
        if (ml && mr) {
          line(ctx, ml, mr, { color, width: 2.5 });
          dot(ctx, ml, 4);
          dot(ctx, mr, 4);
          if (showLabels) label(ctx, 'ширина рта', mid(ml, mr), color);
        }
        break;
      }
      case 'philtrum_ratio': {
        const nose = safe('noseTip');
        const up = safe('upperLip');
        const chin = safe('chin');
        if (nose && up) line(ctx, nose, up, { color, width: 2.5 });
        if (up && chin) line(ctx, up, chin, { color: PALETTE.accent, width: 2.5 });
        if (nose && chin && showLabels) label(ctx, 'фильтрум : подбородок', [(nose[0] + chin[0]) / 2, (nose[1] + chin[1]) / 2], color);
        break;
      }
      case 'gonial_angle':
      case 'jaw_angularity': {
        const jl = safe('jawLeft');
        const jr = safe('jawRight');
        const chin = safe('chin');
        const zl = safe('zygoLeft');
        const zr = safe('zygoRight');
        if (jl && chin && jr) {
          line(ctx, jl, chin, { color, width: 2.5 });
          line(ctx, chin, jr, { color, width: 2.5 });
          line(ctx, jl, zl || jl, { color: PALETTE.accent, width: 2, dash: [6, 4] });
          line(ctx, jr, zr || jr, { color: PALETTE.accent, width: 2, dash: [6, 4] });
          const rl = [jl[0] + 0.02 * (chin[0] - jl[0]), jl[1] - (chin[1] - jl[1]) * 0.6];
          const rr = [jr[0] + 0.02 * (chin[0] - jr[0]), jr[1] - (chin[1] - jr[1]) * 0.6];
          line(ctx, jl, rl, { color, width: 2 });
          line(ctx, jr, rr, { color, width: 2 });
          angleArc(ctx, jl, chin, rl, 24, PALETTE.warn);
          angleArc(ctx, jr, chin, rr, 24, PALETTE.warn);
          dot(ctx, jl, 4.5);
          dot(ctx, jr, 4.5);
          if (showLabels) {
            label(ctx, `${angleBetween(jl, chin, rl).toFixed(0)}°`, [jl[0], jl[1] - 16], color);
            label(ctx, `${angleBetween(jr, chin, rr).toFixed(0)}°`, [jr[0], jr[1] - 16], color);
          }
        }
        break;
      }
      case 'bigonial_width': {
        const jl = safe('jawLeft');
        const jr = safe('jawRight');
        if (jl && jr) {
          line(ctx, jl, jr, { color, width: 2.5 });
          dot(ctx, jl, 4.5);
          dot(ctx, jr, 4.5);
          if (showLabels) label(ctx, 'бигониальная ширина', mid(jl, jr), color);
        }
        break;
      }
      case 'lower_upper_face_ratio': {
        const brow = has('leftBrowInner') && has('rightBrowInner')
          ? mid(P('leftBrowInner'), P('rightBrowInner')) : null;
        const chin = safe('chin');
        const top = safe('foreheadTop');
        if (brow && chin && top) {
          const x = chin[0];
          line(ctx, [x, top[1]], [x, chin[1]], { color: PALETTE.lineSoft, width: 1.5, dash: [5, 4] });
          line(ctx, [x - 30, brow[1]], [x + 30, brow[1]], { color, width: 2.5 });
          line(ctx, [x - 30, chin[1]], [x + 30, chin[1]], { color: PALETTE.accent, width: 2.5 });
          if (showLabels) label(ctx, 'верх : низ', [x, (brow[1] + chin[1]) / 2], color);
        }
        break;
      }
      case 'facial_dimorphism':
      case 'harmony':
      default: {
        const pairs = mirrorPairs(ptsPx.length);
        drawMesh(ctx, ptsPx, pairs, { color: PALETTE.lineSoft, width: 1 });
        ptsPx.forEach((p, i) => { if (i % 2 === 0) dot(ctx, p, 2.5, PALETTE.line); });
        break;
      }
    }
  };

  window.fmFaceOverlay = { drawMetric, indexMap, mirrorPairs, PALETTE, dot, line };
})();

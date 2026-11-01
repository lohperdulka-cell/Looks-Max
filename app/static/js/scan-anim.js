/* ==========================================================================
   FaceMetric — анимация «сканирования» лица.
   Сканирующая линия проходит сверху вниз, точки проявляются по мере её хода,
   затем соединяются линиями ключевых признаков, и канвас переходит в обычный
   режим редактирования.
   ========================================================================== */
(() => {
  'use strict';

  const reduceMotion = () =>
    window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const easeOut = (t) => 1 - Math.pow(1 - t, 3);

  /**
   * Проигрывает анимацию сканирования на канвасе.
   * @param {HTMLCanvasElement} canvas
   * @param {CanvasImageSource} image
   * @param {Array<{x:number,y:number}>} points — нормализованные координаты
   * @param {object} [opts] — { onDone, duration }
   */
  const playScan = (canvas, image, points, opts = {}) => {
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    const ptsPx = points.map((p) => [p.x * W, p.y * H]);

    if (reduceMotion()) {
      opts.onDone && opts.onDone();
      return;
    }

    const sweepMs = opts.duration || 1500;
    const connectMs = 620;
    const start = performance.now();
    const overlay = window.fmFaceOverlay;
    let done = false;

    const finish = () => {
      if (done) return;
      done = true;
      opts.onDone && opts.onDone();
    };

    const drawFrame = (now) => {
      const elapsed = now - start;

      ctx.clearRect(0, 0, W, H);
      ctx.drawImage(image, 0, 0, W, H);

      // Затемняем фото, пока идёт скан, чтобы точки читались.
      const dim = elapsed < sweepMs ? 0.45 : Math.max(0, 0.45 * (1 - (elapsed - sweepMs) / connectMs));
      ctx.fillStyle = `rgba(6, 10, 16, ${dim})`;
      ctx.fillRect(0, 0, W, H);

      if (elapsed < sweepMs) {
        const t = elapsed / sweepMs;
        const y = easeOut(t) * H;

        // Сетка сканера.
        ctx.save();
        ctx.strokeStyle = 'rgba(79, 209, 197, .08)';
        ctx.lineWidth = 1;
        for (let gx = 0; gx < W; gx += 40) {
          ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, H); ctx.stroke();
        }
        for (let gy = 0; gy < H; gy += 40) {
          ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(W, gy); ctx.stroke();
        }
        ctx.restore();

        // Точки, до которых дошёл скан.
        ptsPx.forEach((p) => {
          if (p[1] > y) return;
          const age = Math.min(1, (y - p[1]) / 90);
          const r = 2 + 4 * easeOut(age);
          ctx.save();
          ctx.globalAlpha = 0.35 + 0.65 * age;
          overlay.dot(ctx, p, r, '#4fd1c5');
          ctx.restore();
        });

        // Светящаяся линия сканирования.
        const grad = ctx.createLinearGradient(0, y - 90, 0, y);
        grad.addColorStop(0, 'rgba(79, 209, 197, 0)');
        grad.addColorStop(1, 'rgba(79, 209, 197, .30)');
        ctx.fillStyle = grad;
        ctx.fillRect(0, y - 90, W, 90);

        ctx.save();
        ctx.shadowColor = '#4fd1c5';
        ctx.shadowBlur = 18;
        ctx.strokeStyle = '#8ff5ec';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(W, y);
        ctx.stroke();
        ctx.restore();

        requestAnimationFrame(drawFrame);
        return;
      }

      if (elapsed < sweepMs + connectMs) {
        const t = Math.min(1, (elapsed - sweepMs) / connectMs);
        ptsPx.forEach((p) => overlay.dot(ctx, p, 4, '#4fd1c5'));

        // Линии ключевых признаков проявляются прогрессивно.
        const pairs = overlay.mirrorPairs(ptsPx.length);
        const visible = Math.floor(easeOut(t) * pairs.length);
        ctx.save();
        ctx.strokeStyle = 'rgba(79, 209, 197, .55)';
        ctx.lineWidth = 1;
        ctx.setLineDash([5, 4]);
        ctx.beginPath();
        pairs.slice(0, visible).forEach(([i, j]) => {
          if (i >= ptsPx.length || j >= ptsPx.length) return;
          ctx.moveTo(ptsPx[i][0], ptsPx[i][1]);
          ctx.lineTo(ptsPx[j][0], ptsPx[j][1]);
        });
        ctx.stroke();
        ctx.restore();

        const m = overlay.indexMap(ptsPx.length);
        const pick = (n) => (m[n] !== undefined && m[n] < ptsPx.length ? ptsPx[m[n]] : null);
        if (t > 0.35) {
          const top = pick('foreheadTop');
          const chin = pick('chin');
          if (top && chin) {
            ctx.save();
            ctx.globalAlpha = (t - 0.35) / 0.65;
            ctx.strokeStyle = 'rgba(124, 156, 255, .85)';
            ctx.lineWidth = 1.5;
            ctx.setLineDash([7, 6]);
            ctx.beginPath();
            ctx.moveTo(top[0], top[1]);
            ctx.lineTo(chin[0], chin[1]);
            ctx.stroke();
            ctx.restore();
          }
        }
        requestAnimationFrame(drawFrame);
        return;
      }

      finish();
    };

    requestAnimationFrame(drawFrame);
  };

  window.fmScan = { playScan };
})();

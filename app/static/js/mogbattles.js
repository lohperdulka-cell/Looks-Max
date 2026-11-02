/* ==========================================================================
   FaceMetric — PvP-моггбатлы: очередь, ожидание готовности, раскрытие,
   «MOGGED!» со звуком, лидерборд по PvP-очкам.

   Соперник скрыт (блюр), пока он не нажмёт «Готов». Раскрытие приходит с
   сервера (шаг revealed) — картинку отдаёт сервер, а не клиент, поэтому
   подглядеть в network-вкладке до готовности нельзя.
   ========================================================================== */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const api = window.fmApi;
  const toast = window.fmToast;
  const escapeHtml = window.fmEscape;
  const stage = $('[data-mog-stage]');
  if (!api || !stage) return;

  let current = null;
  let pollTimer = null;

  /* --- Звук «MOGGED!» ------------------------------------------------------
     Играем локальный короткий семпл через HTML5 Audio: файл лежит рядом со
     статикой, поэтому не зависим от чужого хостинга и лицензии, а CSP
     (default-src 'self') не блокирует воспроизведение. Если браузер не дал
     проиграть (например, политика автозапуска), тихо откатываемся на
     синтезированный удар через Web Audio. */
  const playMogged = () => {
    const el = document.querySelector('[data-mog-sound]');
    if (el) {
      try {
        el.currentTime = 0;
        const played = el.play();
        if (played && typeof played.catch === 'function') {
          played.catch(() => synthMogged());
        }
        return;
      } catch {
        /* нет элемента или браузер запретил — синтезируем */
      }
    }
    synthMogged();
  };

  const synthMogged = () => {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const now = ctx.currentTime;
      // Два тона с быстрым спадом — «удар + отзвук».
      [220, 330].forEach((freq, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(freq, now);
        osc.frequency.exponentialRampToValueAtTime(freq * 1.5, now + 0.18);
        gain.gain.setValueAtTime(0.0001, now);
        gain.gain.exponentialRampToValueAtTime(i === 0 ? 0.22 : 0.12, now + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.5);
        osc.connect(gain).connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.55);
      });
      setTimeout(() => ctx.close(), 800);
    } catch { /* звук — необязательное украшение */ }
  };

  const stopPolling = () => {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  };

  const sideCard = (side, reveal, outcome) => {
    const name = escapeHtml(side.name);
    const img = side.image_url
      ? `<img src="${side.image_url}" alt="Фото: ${name}" class="mog-photo${reveal ? '' : ' is-blurred'}">`
      : `<div class="mog-photo mog-photo-hidden" aria-hidden="true">?</div>`;
    // Плашка «MOGGED!» — только на проигравшем, без фона и врезки.
    const stamp = outcome === 'lost'
      ? '<div class="mog-stamp" data-mog-stamp aria-hidden="true">MOGGED!</div>'
      : '';
    const badge = reveal && side.score != null
      ? `<span class="mog-score">${side.score.toFixed(2)}</span>`
      : '';
    const readyTag = side.ready
      ? '<span class="mog-ready-tag is-ready">Готов</span>'
      : '<span class="mog-ready-tag">Ждём…</span>';
    const points = reveal && side.points
      ? `<span class="mog-points-delta ${side.points > 0 ? 'is-up' : 'is-down'}">${side.points > 0 ? '+' : ''}${side.points}</span>`
      : '';
    return `
      <article class="mog-card ${reveal ? 'is-revealed' : ''}${outcome ? ` is-${outcome}` : ''}">
        <div class="mog-photo-wrap">${img}${badge}${points}${stamp}</div>
        <h3 class="mog-name">${name}</h3>
        ${readyTag}
      </article>`;
  };

  const render = (room) => {
    current = room;
    const reveal = room.step === 'revealed';

    if (room.step === 'waiting') {
      stage.innerHTML = `
        <div class="mog-waiting">
          <div class="mog-spinner" aria-hidden="true"></div>
          <h2>Ищем соперника…</h2>
          <p class="muted">Обычно занимает несколько секунд. Можно подождать.</p>
          <button class="btn btn-ghost" type="button" data-mog-leave>Отменить поиск</button>
        </div>`;
      $('[data-mog-leave]').addEventListener('click', leaveRoom);
      schedulePoll(room.room_id);
      return;
    }

    if (room.step === 'opponent_found') {
      stopPolling();
      stage.innerHTML = `
        <div class="mog-duel">
          ${sideCard(room.me, false)}
          <div class="mog-vs" aria-hidden="true">VS</div>
          ${sideCard(room.opponent, false)}
        </div>
        <div class="mog-actions">
          <p class="muted">Соперник найден. Фото скрыто, пока вы не подтвердите готовность.</p>
          <button class="btn btn-primary btn-lg" type="button" data-mog-ready>Готов!</button>
          <button class="btn btn-ghost" type="button" data-mog-leave>Выйти</button>
        </div>`;
      $('[data-mog-ready]').addEventListener('click', markReady);
      $('[data-mog-leave]').addEventListener('click', leaveRoom);
      schedulePoll(room.room_id);
      return;
    }

    // revealed
    stopPolling();
    const iWon = room.winner_id && room.me.user_id === room.winner_id;
    // Плашка «MOGGED!» ставится на проигравшего: победитель видит, кого
    // переиграл, проигравший — свой приговор. Победитель — всегда «я» или
    // «соперник», ничьей тут не бывает.
    const loserIsMe = !iWon;
    const meCard = sideCard(room.me, true, loserIsMe ? 'lost' : 'won');
    const oppCard = sideCard(room.opponent, true, loserIsMe ? 'won' : 'lost');
    const banner = room.is_draw
      ? '<div class="mog-banner is-draw">Ничья</div>'
      : iWon
        ? '<div class="mog-banner is-win">MOGGED!</div>'
        : '<div class="mog-banner is-loss">Вас переиграли</div>';
    stage.innerHTML = `
      ${banner}
      <div class="mog-duel">
        ${meCard}
        <div class="mog-vs" aria-hidden="true">VS</div>
        ${oppCard}
      </div>
      <p class="mog-result muted">${escapeHtml(room.result_text)}</p>
      <div class="mog-actions">
        <button class="btn btn-primary" type="button" data-mog-again>Ещё дуэль</button>
      </div>`;
    $('[data-mog-again]').addEventListener('click', joinQueue);
    if (!room.is_draw) playMogged();
    finishRoom(room.room_id);
    loadProfile();
    loadLeaderboard();
  };

  const schedulePoll = (roomId) => {
    stopPolling();
    pollTimer = setTimeout(async () => {
      try {
        render(await api(`/api/mogbattles/rooms/${roomId}`));
      } catch {
        schedulePoll(roomId);
      }
    }, 2000);
  };

  const joinQueue = async () => {
    stopPolling();
    stage.innerHTML = `
      <div class="mog-waiting">
        <div class="mog-spinner" aria-hidden="true"></div>
        <h2>Подбираем соперника…</h2>
      </div>`;
    try {
      render(await api('/api/mogbattles/queue', { method: 'POST' }));
    } catch (err) {
      renderIdle(err.message);
      toast(err.message || 'Не удалось начать дуэль', 'error');
    }
  };

  const markReady = async () => {
    if (!current) return;
    try {
      render(await api(`/api/mogbattles/rooms/${current.room_id}/ready`, { method: 'POST' }));
    } catch (err) {
      toast(err.message || 'Не удалось подтвердить готовность', 'error');
    }
  };

  const leaveRoom = async () => {
    if (current) {
      try {
        await api(`/api/mogbattles/rooms/${current.room_id}`, { method: 'DELETE' });
      } catch { /* комната могла уже закрыться */ }
    }
    stopPolling();
    current = null;
    renderIdle();
  };

  const finishRoom = async (roomId) => {
    try {
      await api(`/api/mogbattles/rooms/${roomId}/finish`, { method: 'POST' });
    } catch { /* не критично */ }
  };

  const renderIdle = (message) => {
    stage.innerHTML = `
      <div class="mog-idle">
        <div class="mog-idle-art" aria-hidden="true">⚔</div>
        <h2>Готовы сразиться?</h2>
        <p class="muted">${escapeHtml(message) || 'Мы подберём соперника с близкими PvP-очками.'}</p>
        <button class="btn btn-primary btn-lg" type="button" data-mog-join>Найти соперника</button>
      </div>`;
    $('[data-mog-join]').addEventListener('click', joinQueue);
  };

  const loadProfile = async () => {
    try {
      const p = await api('/api/mogbattles/me');
      $('[data-mog-points]').textContent = p.pvp_points;
      $('[data-mog-record]').textContent = `${p.wins}/${p.losses}`;
      $('[data-mog-rank]').textContent = p.rank ?? '—';
    } catch { /* гость */ }
  };

  const loadLeaderboard = async () => {
    const list = $('[data-mog-leaderboard]');
    if (!list) return;
    try {
      const rows = await api('/api/mogbattles/leaderboard?limit=50');
      list.innerHTML = rows.length
        ? rows
            .map((r) => `
              <li class="lb-row ${r.rank <= 3 ? `lb-top lb-top-${r.rank}` : ''}">
                <span class="lb-rank">${r.rank}</span>
                <span class="lb-name">${escapeHtml(r.display_name || '@' + r.username)}</span>
                <span class="lb-elo">${r.pvp_points}</span>
              </li>`)
            .join('')
        : '<li class="muted">Пока никто не сражался.</li>';
    } catch (err) {
      toast(err.message || 'Не удалось загрузить лидерборд', 'error');
    }
  };

  $('[data-mog-join]')?.addEventListener('click', joinQueue);
  (async () => {
    await Promise.all([loadProfile(), loadLeaderboard()]);
  })();
})();

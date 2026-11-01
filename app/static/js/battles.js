/* ==========================================================================
   FaceMetric — мобатлы: пара фото, голосование, лидерборд, батл недели.
   ========================================================================== */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const api = window.fmApi;
  const toast = window.fmToast;
  if (!api || !$('[data-battle-stage]')) return;

  const stage = $('[data-battle-stage]');
  let currentBattle = null;
  let lbScope = 'global';
  let seasonId = null;

  const photoCard = (side, photo) => `
    <button class="battle-card" type="button" data-photo-id="${photo.id}" data-side="${side}">
      <img src="${photo.image_url}" alt="Фото участника" loading="lazy">
      <span class="battle-owner muted small">${photo.owner || 'участник'}</span>
      <span class="battle-votes" data-votes="${side}">0</span>
      <span class="battle-pick">Выбрать</span>
    </button>`;

  const renderBattle = (battle) => {
    currentBattle = battle;
    $('[data-total-votes]').textContent = battle.total_votes;

    const voted = battle.my_vote_photo_id;
    const finished = !battle.is_active || battle.winner_photo_id;

    stage.innerHTML = `
      <div class="battle-pair ${finished ? 'is-finished' : ''}">
        ${photoCard('left', battle.left)}
        <div class="battle-vs" aria-hidden="true">VS</div>
        ${photoCard('right', battle.right)}
      </div>
      <div class="battle-bar">
        <div class="battle-bar-fill" style="width:${barPercent(battle)}%"></div>
      </div>
      <p class="battle-status muted small" data-battle-status></p>
      <button class="btn btn-primary" type="button" data-next-battle>
        ${finished ? 'Следующий батл' : 'Обновить'}
      </button>`;

    $$('[data-votes="left"]').forEach((el) => (el.textContent = battle.votes_left));
    $$('[data-votes="right"]').forEach((el) => (el.textContent = battle.votes_right));

    const status = $('[data-battle-status]');
    if (finished) {
      const winner = battle.winner_photo_id === battle.left.id ? battle.left : battle.right;
      status.textContent = `Батл завершён. Победил ${winner.owner || 'участник'}.`;
      $$('.battle-card').forEach((card) => {
        card.disabled = true;
        if (Number(card.dataset.photoId) === battle.winner_photo_id) card.classList.add('is-winner');
      });
    } else if (voted) {
      status.textContent = 'Ваш голос учтён. Ждём остальные голоса.';
      $$('.battle-card').forEach((card) => {
        card.disabled = true;
        if (Number(card.dataset.photoId) === voted) card.classList.add('is-picked');
      });
    } else {
      status.textContent = 'Выберите фото, которое выглядит лучше.';
    }

    $$('.battle-card:not([disabled])').forEach((card) => {
      card.addEventListener('click', () => castVote(Number(card.dataset.photoId)));
    });
    $('[data-next-battle]').addEventListener('click', loadBattle);
  };

  const barPercent = (battle) => {
    const total = battle.votes_left + battle.votes_right;
    if (!total) return 50;
    return Math.round((battle.votes_left / total) * 100);
  };

  const castVote = async (photoId) => {
    if (!currentBattle) return;
    $$('.battle-card').forEach((card) => (card.disabled = true));
    try {
      const result = await api(`/api/battles/${currentBattle.id}/vote`, {
        method: 'POST',
        body: JSON.stringify({ chosen_photo_id: photoId }),
      });
      renderBattle(result.battle);
      toast(result.detail, result.finished ? 'success' : 'info');
      if (result.finished) await Promise.all([loadLeaderboard(), refreshMyElo()]);
    } catch (err) {
      toast(err.message || 'Не удалось проголосовать', 'error');
      loadBattle();
    }
  };

  const loadBattle = async () => {
    stage.innerHTML = `
      <div class="battle-skeleton">
        <div class="skeleton skeleton-block"></div>
        <div class="skeleton skeleton-block"></div>
      </div>`;
    try {
      const battle = await api('/api/battles/next');
      renderBattle(battle);
    } catch (err) {
      stage.innerHTML = `
        <div class="empty-state">
          <h2>Батл недоступен</h2>
          <p class="muted">${err.message || 'Нужно публичное фото хорошего качества.'}</p>
          <a class="btn btn-primary" href="/rate">Загрузить фото</a>
        </div>`;
    }
  };

  const renderLeaderboard = (rows) => {
    const list = $('[data-leaderboard]');
    if (!list) return;
    if (!rows.length) {
      list.innerHTML = '<li class="muted">Пока никто не участвовал.</li>';
      return;
    }
    list.innerHTML = rows
      .map((row) => `
        <li class="lb-row ${row.rank <= 3 ? `lb-top lb-top-${row.rank}` : ''}">
          <span class="lb-rank">${row.rank}</span>
          <span class="lb-name">${row.display_name || '@' + row.username}</span>
          <span class="lb-wl muted small">${row.wins}/${row.losses}</span>
          <span class="lb-elo">${row.elo}</span>
        </li>`)
      .join('');
  };

  const loadLeaderboard = async () => {
    try {
      const query = lbScope === 'season' && seasonId ? `?season_id=${seasonId}` : '?limit=50';
      renderLeaderboard(await api(`/api/battles/leaderboard${query}`));
    } catch (err) {
      toast(err.message || 'Не удалось загрузить лидерборд', 'error');
    }
  };

  const refreshMyElo = async () => {
    try {
      const profile = await api('/api/profile');
      $('[data-my-elo]').textContent = profile.elo;
    } catch { /* гость */ }
  };

  const loadSeason = async () => {
    try {
      const season = await api('/api/battles/season');
      seasonId = season.id;
      $('[data-season-title]').textContent = season.title;
      $('[data-season-dates]').textContent =
        `${new Date(season.starts_at).toLocaleDateString('ru-RU')} — ${new Date(season.ends_at).toLocaleDateString('ru-RU')}`;
    } catch { /* сезон необязателен */ }
  };

  $$('[data-lb-scope]').forEach((btn) => {
    btn.addEventListener('click', () => {
      lbScope = btn.dataset.lbScope;
      $$('[data-lb-scope]').forEach((b) => b.classList.toggle('is-active', b === btn));
      loadLeaderboard();
    });
  });

  (async () => {
    await Promise.all([loadSeason(), loadLeaderboard(), refreshMyElo()]);
    await loadBattle();
  })();
})();

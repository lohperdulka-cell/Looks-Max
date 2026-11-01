/* ==========================================================================
   FaceMetric — базовый JS (vanilla, без зависимостей)
   Тема, тосты, мобильная навигация, reveal-анимации, счётчики,
   формы аутентификации, CSRF, скелетоны, онбординг-тур.
   ========================================================================== */
(() => {
  'use strict';

  /* --- Утилиты ------------------------------------------------------------- */
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const getCookie = (name) => {
    const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : null;
  };

  // Экранирование для подстановки в innerHTML (данные из API).
  const escapeHtml = (value) =>
    String(value ?? '').replace(/[&<>"']/g, (ch) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
    ));
  window.fmEscape = escapeHtml;

  /* --- Тема (localStorage) ------------------------------------------------- */
  const THEME_KEY = 'fm-theme';
  const applyTheme = (theme) => {
    document.documentElement.setAttribute('data-theme', theme);
    const meta = $('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme === 'light' ? '#f6f8fb' : '#0b0f14');
  };
  const initTheme = () => {
    const saved = localStorage.getItem(THEME_KEY);
    const prefersLight = window.matchMedia('(prefers-color-scheme: light)').matches;
    applyTheme(saved || (prefersLight ? 'light' : 'dark'));
  };
  initTheme();

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-theme-toggle]');
    if (!btn) return;
    const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    applyTheme(next);
    localStorage.setItem(THEME_KEY, next);
  });

  /* --- Toast-уведомления --------------------------------------------------- */
  const TOAST_ICONS = { success: '✓', error: '!', warn: '⚠', info: 'ℹ' };
  const toast = (message, type = 'info', timeout = 4200) => {
    const stack = $('[data-toast-stack]');
    if (!stack) return;
    const el = document.createElement('div');
    el.className = `toast is-${type}`;
    el.setAttribute('role', type === 'error' ? 'alert' : 'status');
    el.innerHTML = `<strong aria-hidden="true">${TOAST_ICONS[type] || ''}</strong> ${message}`;
    stack.appendChild(el);
    const remove = () => {
      el.classList.add('is-hiding');
      setTimeout(() => el.remove(), 240);
    };
    if (timeout > 0) setTimeout(remove, timeout);
    el.addEventListener('click', remove);
  };
  window.fmToast = toast;

  /* --- Мобильная навигация ------------------------------------------------- */
  const navToggle = $('[data-nav-toggle]');
  const nav = $('#primary-nav');
  if (navToggle && nav) {
    navToggle.addEventListener('click', () => {
      const open = nav.classList.toggle('is-open');
      navToggle.setAttribute('aria-expanded', String(open));
    });
    nav.addEventListener('click', (e) => {
      if (e.target.tagName === 'A') {
        nav.classList.remove('is-open');
        navToggle.setAttribute('aria-expanded', 'false');
      }
    });
  }

  /* --- Reveal-анимации при скролле ----------------------------------------- */
  const revealObserver = 'IntersectionObserver' in window
    ? new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible');
            revealObserver.unobserve(entry.target);
          }
        });
      }, { threshold: 0.12, rootMargin: '0px 0px -40px' })
    : null;

  const observeReveals = (root = document) => {
    $$('.reveal', root).forEach((el) => {
      if (revealObserver) revealObserver.observe(el);
      else el.classList.add('is-visible');
    });
  };
  observeReveals();

  /* --- Счётчики (анимация чисел) ------------------------------------------- */
  const animateCounter = (el) => {
    const target = parseFloat(el.dataset.counter || '0');
    const decimals = parseInt(el.dataset.decimals || '0', 10);
    if (!isFinite(target) || target <= 0) { el.textContent = target.toFixed(decimals); return; }
    const duration = 1100;
    const start = performance.now();
    const step = (now) => {
      const p = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = (target * eased).toFixed(decimals);
      if (p < 1) requestAnimationFrame(step);
      else el.textContent = target.toFixed(decimals);
    };
    requestAnimationFrame(step);
  };

  const counterObserver = 'IntersectionObserver' in window
    ? new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            animateCounter(entry.target);
            counterObserver.unobserve(entry.target);
          }
        });
      }, { threshold: 0.4 })
    : null;

  $$('[data-counter]').forEach((el) => {
    if (counterObserver) counterObserver.observe(el);
    else animateCounter(el);
  });

  /* --- Skeleton-хелперы ---------------------------------------------------- */
  window.fmSkeleton = {
    show(container, count = 3, className = 'skeleton-card') {
      if (!container) return;
      container.dataset.prev = container.innerHTML;
      container.innerHTML = Array.from({ length: count })
        .map(() => `<div class="skeleton ${className}"></div>`)
        .join('');
    },
    hide(container) {
      if (!container || container.dataset.prev === undefined) return;
      container.innerHTML = container.dataset.prev;
      delete container.dataset.prev;
      observeReveals(container);
    },
  };

  /* --- Fetch с CSRF -------------------------------------------------------- */
  const api = async (url, { method = 'GET', body, headers = {} } = {}) => {
    const opts = {
      method,
      headers: { Accept: 'application/json', ...headers },
      credentials: 'same-origin',
    };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    if (method !== 'GET' && method !== 'HEAD') {
      const csrf = getCookie('fm_csrf');
      if (csrf) opts.headers['X-CSRF-Token'] = csrf;
    }
    const resp = await fetch(url, opts);
    let data = null;
    const text = await resp.text();
    if (text) { try { data = JSON.parse(text); } catch { data = { detail: text }; } }
    if (!resp.ok) {
      const err = new Error((data && data.detail) || `HTTP ${resp.status}`);
      err.status = resp.status;
      err.payload = data;
      throw err;
    }
    return data;
  };
  window.fmApi = api;

  /* --- Формы аутентификации ------------------------------------------------ */
  const showFormError = (form, message) => {
    const box = $('[data-form-error]', form);
    if (!box) return;
    box.textContent = message;
    box.hidden = false;
  };
  const clearFormError = (form) => {
    const box = $('[data-form-error]', form);
    if (box) { box.hidden = true; box.textContent = ''; }
  };

  $$('[data-auth-form]').forEach((form) => {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearFormError(form);
      const submit = $('button[type="submit"]', form);
      const mode = form.dataset.mode;
      const fd = new FormData(form);
      const payload = {
        username: (fd.get('username') || '').toString().trim(),
        password: (fd.get('password') || '').toString(),
      };
      if (mode === 'register') {
        const email = (fd.get('email') || '').toString().trim();
        if (email) payload.email = email;
        payload.biometric_consent = fd.get('biometric_consent') === 'on';
        if (!payload.biometric_consent) {
          showFormError(form, 'Нужно согласие на обработку биометрии.');
          return;
        }
      }

      submit.setAttribute('aria-busy', 'true');
      submit.disabled = true;
      try {
        await api(`/api/auth/${mode}`, { method: 'POST', body: payload });
        toast(mode === 'register' ? 'Аккаунт создан. Добро пожаловать!' : 'Вы вошли.', 'success');
        const next = new URLSearchParams(location.search).get('next') || '/profile';
        setTimeout(() => { location.href = next; }, 420);
      } catch (err) {
        showFormError(form, err.message || 'Не удалось выполнить запрос');
        toast(err.message || 'Ошибка', 'error');
      } finally {
        submit.removeAttribute('aria-busy');
        submit.disabled = false;
      }
    });
  });

  /* --- Форма восстановления пароля ----------------------------------------- */
  const resetForm = $('[data-reset-form]');
  if (resetForm) {
    resetForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      clearFormError(resetForm);
      const success = $('[data-form-success]', resetForm);
      const submit = $('button[type="submit"]', resetForm);
      const email = ($('input[name="email"]', resetForm).value || '').trim();
      submit.disabled = true;
      submit.setAttribute('aria-busy', 'true');
      try {
        const data = await api('/api/auth/password-reset', { method: 'POST', body: { email } });
        if (success) { success.textContent = data.detail; success.hidden = false; }
        toast('Запрос отправлен', 'success');
      } catch (err) {
        showFormError(resetForm, err.message);
      } finally {
        submit.disabled = false;
        submit.removeAttribute('aria-busy');
      }
    });
  }

  /* --- Актуальность состояния авторизации в шапке -------------------------- */
  const authSlot = $('[data-auth-slot]');
  if (authSlot) {
    api('/api/auth/me')
      .then((user) => {
        authSlot.innerHTML =
          `<span class="nav-user muted">${user.display_name || user.username}</span>` +
          `<button class="btn btn-ghost" type="button" data-logout>Выйти</button>`;
        const out = $('[data-logout]', authSlot);
        if (out) {
          out.addEventListener('click', async () => {
            try { await api('/api/auth/logout', { method: 'POST' }); } catch { /* ignore */ }
            toast('Вы вышли из аккаунта', 'info');
            setTimeout(() => { location.href = '/'; }, 350);
          });
        }
      })
      .catch(() => { /* гость — оставляем ссылки на вход/регистрацию */ });
  }

  /* --- Онбординг-тур ------------------------------------------------------- */
  const ONBOARDING_KEY = 'fm-onboarding-seen';
  const ONBOARDING_STEPS = [
    { text: 'Добро пожаловать в FaceMetric! Это платформа метрик лица.', target: null },
    { text: 'Загрузите фото в разделе «Оценка» — модель найдёт точки лица.', target: '[href="/rate"]' },
    { text: 'В профиле — история, прогресс и достижения.', target: '[data-auth-slot]' },
    { text: 'ИИ-консультант подскажет, что улучшить, с учётом ваших метрик.', target: '[href="/chat"]' },
  ];

  const startOnboarding = () => {
    let index = 0;
    const overlay = document.createElement('div');
    overlay.className = 'toast';
    overlay.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);bottom:24px;z-index:95;max-width:min(92vw,420px)';
    const render = () => {
      const step = ONBOARDING_STEPS[index];
      overlay.innerHTML =
        `<div>${step.text}</div>` +
        `<div style="display:flex;gap:8px;margin-top:10px;justify-content:flex-end">` +
        `<button class="btn btn-ghost" data-skip>Пропустить</button>` +
        `<button class="btn btn-primary" data-next>${index === ONBOARDING_STEPS.length - 1 ? 'Готово' : 'Далее'}</button>` +
        `</div>`;
      overlay.querySelector('[data-skip]').onclick = finish;
      overlay.querySelector('[data-next]').onclick = () => {
        index += 1;
        if (index >= ONBOARDING_STEPS.length) finish();
        else render();
      };
      if (step.target) {
        const el = $(step.target);
        if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.classList.add('is-visible'); }
      }
    };
    const finish = () => {
      overlay.remove();
      localStorage.setItem(ONBOARDING_KEY, '1');
    };
    document.body.appendChild(overlay);
    render();
  };

  if (!localStorage.getItem(ONBOARDING_KEY) && document.body.dataset.onboarding === '1') {
    setTimeout(startOnboarding, 900);
  }

  /* --- Публичный API ------------------------------------------------------- */
  window.fm = { api, toast, getCookie, observeReveals, escapeHtml };
})();

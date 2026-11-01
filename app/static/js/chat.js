/* ==========================================================================
   FaceMetric — ИИ-консультант: SSE-стриминг ответа, быстрые промпты, план.
   ========================================================================== */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const api = window.fmApi;
  const toast = window.fmToast;
  const escapeHtml = window.fmEscape;
  if (!api || !$('[data-chat-form]')) return;

  let sessionId = null;
  let busy = false;

  const scroll = () => {
    const box = $('[data-chat-scroll]');
    box.scrollTop = box.scrollHeight;
  };

  const hideEmpty = () => {
    const empty = $('[data-chat-empty]');
    if (empty) empty.remove();
  };

  const addBubble = (role, text = '') => {
    hideEmpty();
    const box = $('[data-chat-scroll]');
    const bubble = document.createElement('div');
    bubble.className = `chat-bubble chat-${role}`;
    bubble.innerHTML = `<div class="chat-text">${escapeHtml(text)}</div>`;
    box.appendChild(bubble);
    scroll();
    return bubble.querySelector('.chat-text');
  };

  const renderHistory = (messages) => {
    const box = $('[data-chat-scroll]');
    box.innerHTML = '';
    if (!messages.length) {
      box.innerHTML = '<div class="chat-empty"><p class="muted">Задайте вопрос или выберите быстрый промпт ниже.</p></div>';
      return;
    }
    messages.forEach((m) => addBubble(m.role, m.content));
  };

  const streamAnswer = async (message) => {
    const bubble = addBubble('assistant', '');
    bubble.classList.add('is-typing');

    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
    if (!response.ok || !response.body) {
      bubble.classList.remove('is-typing');
      bubble.textContent = 'Не удалось получить ответ.';
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let text = '';

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const blocks = buffer.split('\n\n');
      buffer = blocks.pop() || '';
      for (const block of blocks) {
        const eventLine = block.split('\n').find((l) => l.startsWith('event: '));
        const dataLine = block.split('\n').find((l) => l.startsWith('data: '));
        if (!dataLine) continue;
        const event = eventLine ? eventLine.slice(7).trim() : 'message';
        let data = {};
        try { data = JSON.parse(dataLine.slice(6)); } catch { /* пропускаем */ }

        if (event === 'meta' && data.session_id) sessionId = data.session_id;
        if (event === 'delta') {
          text += data.text || '';
          bubble.textContent = text;
          scroll();
        }
        if (event === 'error') {
          toast(data.detail || 'Ошибка стрима', 'error');
        }
        if (event === 'done') {
          bubble.classList.remove('is-typing');
          if (data.offline) bubble.dataset.offline = '1';
        }
      }
    }
    bubble.classList.remove('is-typing');
  };

  const send = async (text) => {
    if (busy || !text.trim()) return;
    busy = true;
    $('[data-chat-send]').disabled = true;
    addBubble('user', text);
    try {
      await streamAnswer(text);
    } catch (err) {
      toast(err.message || 'Сеть недоступна', 'error');
    } finally {
      busy = false;
      $('[data-chat-send]').disabled = false;
    }
  };

  const renderQuickPrompts = (prompts) => {
    const box = $('[data-quick-prompts]');
    box.innerHTML = prompts
      .map((p) => `<button class="quick-prompt" type="button" data-prompt="${escapeHtml(p.prompt)}">${escapeHtml(p.title)}</button>`)
      .join('');
    $$('[data-prompt]').forEach((btn) => {
      btn.addEventListener('click', () => send(btn.dataset.prompt));
    });
  };

  const generatePlan = async () => {
    const days = Number($('[data-plan-days]').value) || 30;
    const btn = $('[data-plan-generate]');
    btn.disabled = true;
    try {
      const plan = await api('/api/chat/plans', {
        method: 'POST',
        body: JSON.stringify({ horizon_days: days }),
      });
      $('[data-plan-result]').innerHTML =
        `<article class="plan-card"><h3>${escapeHtml(plan.title)}</h3><pre class="plan-body">${escapeHtml(plan.content_md)}</pre></article>`;
      toast('План готов', 'success');
    } catch (err) {
      toast(err.message || 'Не удалось создать план', 'error');
    } finally {
      btn.disabled = false;
    }
  };

  $('[data-chat-form]').addEventListener('submit', (event) => {
    event.preventDefault();
    const input = $('.chat-input');
    const text = input.value;
    input.value = '';
    send(text);
  });

  $('[data-plan-generate]').addEventListener('click', generatePlan);

  (async () => {
    try {
      const [meta, session] = await Promise.all([
        api('/api/chat/meta'),
        api('/api/chat/session'),
      ]);
      sessionId = session.id;
      renderHistory(session.messages);
      renderQuickPrompts(meta.quick_prompts);
      if (!meta.llm_enabled) {
        toast('LLM не подключён — работает встроенный советник по вашим метрикам', 'info');
      }
    } catch (err) {
      toast(err.message || 'Не удалось инициализировать чат', 'error');
    }
  })();
})();

/* ==========================================================================
   FaceMetric — форма обратной связи на странице контактов.
   ========================================================================== */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const api = window.fmApi;
  const toast = window.fmToast;
  const form = $('[data-contact-form]');
  if (!api || !form) return;

  const status = $('[data-contact-status]');

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    if (status) status.textContent = 'Отправляем…';

    try {
      const result = await api('/api/feedback', {
        method: 'POST',
        body: JSON.stringify({
          subject: form.subject.value.trim(),
          name: form.name.value.trim() || null,
          email: form.email.value.trim() || null,
          message: form.message.value.trim(),
        }),
      });
      form.reset();
      if (status) status.textContent = `Обращение №${result.id} принято. Ответим на указанный email.`;
      toast('Сообщение отправлено', 'success');
    } catch (err) {
      if (status) status.textContent = err.message || 'Не удалось отправить.';
      toast(err.message || 'Не удалось отправить сообщение', 'error');
    } finally {
      submit.disabled = false;
    }
  });
})();

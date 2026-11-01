/* ==========================================================================
   FaceMetric — маячок аналитики просмотров.
   Обезличенный POST /api/analytics/ping: путь и referer, без персональных данных.
   ========================================================================== */
(() => {
  'use strict';

  const endpoint = '/api/analytics/ping';
  const path = window.location.pathname;
  // Служебные и приватные разделы не считаем: там нет публичного контента.
  const skip = ['/api/', '/admin', '/profile', '/report', '/r/', '/chat', '/exercises'];
  if (skip.some((prefix) => path.startsWith(prefix))) return;

  const payload = JSON.stringify({ path, referer: document.referrer || null });

  const send = () => {
    if (navigator.sendBeacon) {
      // sendBeacon переживает уход со страницы и не блокирует навигацию.
      navigator.sendBeacon(endpoint, new Blob([payload], { type: 'application/json' }));
      return;
    }
    fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
      keepalive: true,
    }).catch(() => {});
  };

  if (document.readyState === 'complete') send();
  else window.addEventListener('load', send, { once: true });
})();

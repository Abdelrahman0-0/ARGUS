/* ARGUS service worker: receives push alerts and handles the buttons on them. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (_) { /* ignore */ }
  const opts = {
    body: d.body || '',
    tag: d.tag || 'argus',
    renotify: true,
    requireInteraction: !!d.urgent,
    icon: '/icon-192.png',
    badge: '/icon-badge.png',
    dir: 'rtl',
    lang: 'ar',
    vibrate: d.urgent ? [300, 120, 300, 120, 600] : [120],
    timestamp: Date.now(),
    data: { alert: !!d.alert },
    actions: d.alert ? [
      { action: 'lock', title: 'قفل الجهاز' },
      { action: 'shot', title: 'لقطة شاشة' },
    ] : [],
  };
  event.waitUntil(self.registration.showNotification(d.title || 'ARGUS', opts));
});

async function quickAction(name, okText) {
  try {
    const r = await fetch('/api/action/' + name, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'X-ARGUS': '1', 'Content-Type': 'application/json' }, body: '{}',
    });
    if (!r.ok) throw new Error(r.status === 401 ? 'سجّل دخول من التطبيق الأول' : 'فشل');
    await self.registration.showNotification('ARGUS', { body: okText, tag: 'argus-result', icon: '/icon-192.png', dir: 'rtl', lang: 'ar' });
  } catch (e) {
    await self.registration.showNotification('ARGUS', { body: 'ما نفعش: ' + e.message, tag: 'argus-result', icon: '/icon-192.png', dir: 'rtl', lang: 'ar' });
  }
}

async function openApp(hash) {
  const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
  for (const c of all) {
    if ('focus' in c) {
      await c.focus();
      if (hash === '#screenshot') c.postMessage({ type: 'screenshot' });
      return;
    }
  }
  return self.clients.openWindow('/' + (hash || ''));
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  if (event.action === 'lock') event.waitUntil(quickAction('lock', 'الجهاز اتقفل'));
  else if (event.action === 'shot') event.waitUntil(openApp('#screenshot'));
  else event.waitUntil(openApp(''));
});

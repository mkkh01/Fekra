self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_) {
    payload = { title: 'Weeg', body: event.data ? event.data.text() : 'لديك تحديث جديد' };
  }
  const title = payload.title || 'Weeg';
  const options = {
    body: payload.body || 'حدث جديد في نظام التداول الورقي',
    tag: payload.tag || 'weeg-trade-event',
    renotify: true,
    requireInteraction: false,
    dir: 'rtl',
    lang: 'ar',
    icon: '/static/weeg-icon.svg',
    badge: '/static/weeg-icon.svg',
    data: { ...(payload.data || {}), url: payload.url || '/' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = event.notification.data?.url || '/';
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const existing = clients.find((client) => 'focus' in client);
    if (existing) {
      await existing.focus();
      if ('navigate' in existing) await existing.navigate(target);
    } else {
      await self.clients.openWindow(target);
    }
  })());
});

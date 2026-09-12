/* Service Worker — وكيل Odoo. تخزين هيكل التطبيق + عمل دون إنترنت. */
const VERSION = 'v1';
const CACHE = 'odoo-agent-' + VERSION;
const SHELL = [
  '/static/css/style.css',
  '/static/js/core.js',
  '/offline.html',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/manifest.webmanifest',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('message', (e) => { if (e.data === 'skipWaiting') self.skipWaiting(); });

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  // التنقّل بين الصفحات: الشبكة أولاً، ثم offline.html
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(() => caches.match('/offline.html')));
    return;
  }
  // الأصول الثابتة: الكاش أولاً
  if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); return res;
    }).catch(() => hit)));
    return;
  }
  // GET /api/*: الشبكة أولاً مع كاش احتياطي (آخر بيانات معروفة دون إنترنت)
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(fetch(req).then((res) => {
      const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); return res;
    }).catch(() => caches.match(req)));
    return;
  }
});

/* ===== إشعارات Push ===== */
self.addEventListener('push', (e) => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch (err) { data = { body: e.data && e.data.text() }; }
  const title = data.title || 'وكيل Odoo';
  e.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    dir: 'rtl', lang: 'ar',
    data: { url: data.url || '/dashboard' },
    tag: data.tag || 'odoo-agent',
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || '/dashboard';
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    for (const c of list) { if ('focus' in c) { c.navigate(target); return c.focus(); } }
    if (clients.openWindow) return clients.openWindow(target);
  }));
});

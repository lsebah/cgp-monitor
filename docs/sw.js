// CGP Monitor - Service Worker
// Bump CACHE_NAME whenever app.js/index.html/style.css change substantially.
const CACHE_NAME = 'cgp-monitor-v5';

self.addEventListener('install', e => {
    self.skipWaiting();
    // Precache only the current page's base dir, let runtime fetches populate rest.
    e.waitUntil(caches.open(CACHE_NAME));
});

self.addEventListener('activate', e => {
    e.waitUntil(Promise.all([
        self.clients.claim(),
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        ),
    ]));
});

self.addEventListener('fetch', e => {
    if (e.request.method !== 'GET') return;
    const url = new URL(e.request.url);
    // Only handle same-origin requests
    if (url.origin !== self.location.origin) return;

    const isAppShell = /\.(html|js|css|json)$/i.test(url.pathname) || url.pathname.endsWith('/');

    if (isAppShell) {
        // Network-first: always try to fetch latest, fall back to cache offline.
        e.respondWith(
            fetch(e.request).then(r => {
                if (r && r.status === 200) {
                    const copy = r.clone();
                    caches.open(CACHE_NAME).then(c => c.put(e.request, copy)).catch(() => {});
                }
                return r;
            }).catch(() => caches.match(e.request))
        );
    } else {
        // Cache-first for static assets (icons, etc.)
        e.respondWith(
            caches.match(e.request).then(r => r || fetch(e.request).catch(() => new Response('', { status: 404 })))
        );
    }
});

self.addEventListener('message', e => {
    if (e.data && e.data.type === 'SKIP_WAITING') self.skipWaiting();
});

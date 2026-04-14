// CGP Monitor - Service Worker
// Bump CACHE_NAME whenever app.js/index.html/style.css change substantially.
const CACHE_NAME = 'cgp-monitor-v4';
const ASSETS = [
    '/cgp-monitor/',
    '/cgp-monitor/index.html',
    '/cgp-monitor/style.css',
    '/cgp-monitor/app.js',
    '/cgp-monitor/manifest.json',
];

self.addEventListener('install', e => {
    self.skipWaiting();
    e.waitUntil(
        caches.open(CACHE_NAME).then(c =>
            Promise.allSettled(ASSETS.map(a => c.add(a).catch(() => null)))
        )
    );
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
    const url = new URL(e.request.url);
    const isAppShell = /\.(html|js|css|json)$/i.test(url.pathname) || url.pathname.endsWith('/');

    if (isAppShell) {
        // Network-first: always try to fetch latest, fall back to cache offline.
        e.respondWith(
            fetch(e.request).then(r => {
                const copy = r.clone();
                caches.open(CACHE_NAME).then(c => c.put(e.request, copy)).catch(() => {});
                return r;
            }).catch(() => caches.match(e.request))
        );
    } else {
        // Cache-first for static assets (icons, etc.)
        e.respondWith(
            caches.match(e.request).then(r => r || fetch(e.request))
        );
    }
});

// Allow the page to force an update via postMessage
self.addEventListener('message', e => {
    if (e.data && e.data.type === 'SKIP_WAITING') self.skipWaiting();
});

const CACHE_NAME = 'cgp-monitor-v2';
const ASSETS = ['/', '/index.html', '/style.css', '/app.js'];

self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(ASSETS)));
    self.skipWaiting();
});

self.addEventListener('activate', e => {
    e.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', e => {
    // Network-first for everything, cache fallback for offline.
    // Avoids stale HTML/JS/CSS after deploys.
    e.respondWith(
        fetch(e.request)
            .then(r => {
                if (r && r.status === 200 && e.request.method === 'GET') {
                    const copy = r.clone();
                    caches.open(CACHE_NAME).then(c => c.put(e.request, copy));
                }
                return r;
            })
            .catch(() => caches.match(e.request))
    );
});

const CACHE_NAME = 'cgp-monitor-v1';
const ASSETS = ['/', '/index.html', '/style.css', '/app.js'];

self.addEventListener('install', e => {
    e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(ASSETS)));
    self.skipWaiting();
});

self.addEventListener('activate', e => {
    e.waitUntil(caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ));
});

self.addEventListener('fetch', e => {
    if (e.request.url.includes('data/')) {
        // Network-first for data files
        e.respondWith(
            fetch(e.request)
                .then(r => { caches.open(CACHE_NAME).then(c => c.put(e.request, r.clone())); return r; })
                .catch(() => caches.match(e.request))
        );
    } else {
        // Cache-first for assets
        e.respondWith(
            caches.match(e.request).then(r => r || fetch(e.request))
        );
    }
});

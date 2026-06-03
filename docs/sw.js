// CGP Monitor - SELF-DESTRUCTING service worker.
// The old SW was trapping users on stale cached HTML. This version clears all
// caches, unregisters itself, and reloads every open tab with fresh content.
self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        try {
            const keys = await caches.keys();
            await Promise.all(keys.map(k => caches.delete(k)));
        } catch (e) {}
        await self.registration.unregister();
        const clients = await self.clients.matchAll({ type: 'window' });
        for (const client of clients) {
            try { client.navigate(client.url); } catch (e) {}
        }
    })());
});

// Pass-through: never serve from cache.
self.addEventListener('fetch', () => {});

/**
 * Marketplace Scraper — Service Worker
 * Strategy:
 *   - App shell (/,  /manifest.json, /icons/*): cache-first, update in background
 *   - API calls (/api/*) and WebSocket (/ws): bypass — always network
 *   - CDN assets (tailwind, alpine): stale-while-revalidate
 */

const CACHE_NAME = 'scraper-shell-v1';

const PRECACHE = [
  '/',
  '/manifest.json',
  '/icons/icon.svg',
  '/icons/icon-maskable.svg',
];

// ── Install ──────────────────────────────────────────────────────────────────

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE))
  );
  self.skipWaiting();
});

// ── Activate ─────────────────────────────────────────────────────────────────

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ── Fetch ────────────────────────────────────────────────────────────────────

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle GET
  if (request.method !== 'GET') return;

  // Skip API calls and WebSocket — never cache, always network
  if (url.pathname.startsWith('/api/') || url.pathname === '/ws') return;

  // Skip cross-origin requests (Tailwind CDN, Alpine CDN) — browser handles
  if (url.origin !== self.location.origin) return;

  // Cache-first with background revalidation for same-origin assets
  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cached = await cache.match(request);
      const networkFetch = fetch(request)
        .then((response) => {
          if (response.ok) cache.put(request, response.clone());
          return response;
        })
        .catch(() => cached); // offline fallback to cached

      // Return cached immediately if available, revalidate in background
      return cached || networkFetch;
    })
  );
});

const CACHE_NAME = 'tradelab-v1';
const PRECACHE_URLS = [
  '/',
  '/static/favicon.svg',
  '/static/icon-180.png',
  '/static/manifest.json',
  'https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css',
  'https://cdn.tailwindcss.com',
  'https://unpkg.com/htmx.org@2.0.4',
  'https://cdn.plot.ly/plotly-2.35.2.min.js',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  // API/동적 요청은 네트워크 우선
  if (url.pathname.startsWith('/api/') || e.request.method !== 'GET') {
    return;
  }

  // 정적 자산은 캐시 우선, 나머지는 네트워크 우선 + 캐시 폴백
  if (url.pathname.startsWith('/static/') || PRECACHE_URLS.includes(e.request.url)) {
    e.respondWith(
      caches.match(e.request).then((cached) => cached || fetch(e.request))
    );
  } else {
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const clone = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(e.request, clone));
          return res;
        })
        .catch(() => caches.match(e.request))
    );
  }
});

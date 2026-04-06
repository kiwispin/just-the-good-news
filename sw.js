/* ============================================================
   Just The Good News — Service Worker
   Strategy:
     • App shell (CSS, JS, fonts) → Cache-first, update in background
     • Article pages (HTML)       → Network-first, fall back to cache
     • Images                     → Cache-first (long-lived)
     • Everything else            → Network-first
   ============================================================ */

const CACHE_VERSION  = 'jtgn-v1';
const SHELL_CACHE    = `${CACHE_VERSION}-shell`;
const CONTENT_CACHE  = `${CACHE_VERSION}-content`;
const IMAGE_CACHE    = `${CACHE_VERSION}-images`;

// App shell — cached on install, refreshed on SW update
const SHELL_ASSETS = [
  '/',
  '/css/style.css',
  '/js/filter.js',
  '/js/search.js',
  'https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Source+Serif+4:ital,wght@0,400;0,600;1,400&display=swap',
];

// ── Install: pre-cache the shell ─────────────────────────────────────────────
self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(SHELL_CACHE).then(function (cache) {
      return cache.addAll(SHELL_ASSETS);
    }).then(function () {
      return self.skipWaiting();
    })
  );
});

// ── Activate: remove stale caches ────────────────────────────────────────────
self.addEventListener('activate', function (event) {
  var validCaches = [SHELL_CACHE, CONTENT_CACHE, IMAGE_CACHE];
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (key) {
          return !validCaches.includes(key);
        }).map(function (key) {
          return caches.delete(key);
        })
      );
    }).then(function () {
      return self.clients.claim();
    })
  );
});

// ── Fetch: route requests to the right strategy ──────────────────────────────
self.addEventListener('fetch', function (event) {
  var url = new URL(event.request.url);

  // Skip non-GET and cross-origin requests we don't handle
  if (event.request.method !== 'GET') return;
  if (url.origin !== location.origin &&
      !url.hostname.endsWith('fonts.googleapis.com') &&
      !url.hostname.endsWith('fonts.gstatic.com') &&
      !url.hostname.endsWith('unsplash.com')) {
    return;
  }

  // Images → cache-first (they don't change once published)
  if (/\.(png|jpe?g|gif|svg|webp|avif)(\?.*)?$/.test(url.pathname)) {
    event.respondWith(cacheFirst(event.request, IMAGE_CACHE));
    return;
  }

  // Shell assets (CSS / JS / fonts) → cache-first, revalidate in bg
  if (/\.(css|js)(\?.*)?$/.test(url.pathname) ||
      url.hostname.endsWith('fonts.googleapis.com') ||
      url.hostname.endsWith('fonts.gstatic.com')) {
    event.respondWith(staleWhileRevalidate(event.request, SHELL_CACHE));
    return;
  }

  // HTML navigation → network-first with cache fallback
  if (event.request.mode === 'navigate' ||
      event.request.headers.get('accept').includes('text/html')) {
    event.respondWith(networkFirst(event.request, CONTENT_CACHE));
    return;
  }

  // Everything else → network-first
  event.respondWith(networkFirst(event.request, CONTENT_CACHE));
});

// ── Strategies ───────────────────────────────────────────────────────────────

function cacheFirst(request, cacheName) {
  return caches.open(cacheName).then(function (cache) {
    return cache.match(request).then(function (cached) {
      if (cached) return cached;
      return fetch(request).then(function (response) {
        if (response.ok) cache.put(request, response.clone());
        return response;
      });
    });
  });
}

function staleWhileRevalidate(request, cacheName) {
  return caches.open(cacheName).then(function (cache) {
    return cache.match(request).then(function (cached) {
      var networkFetch = fetch(request).then(function (response) {
        if (response.ok) cache.put(request, response.clone());
        return response;
      }).catch(function () { return cached; });
      return cached || networkFetch;
    });
  });
}

function networkFirst(request, cacheName) {
  return fetch(request).then(function (response) {
    if (response.ok) {
      caches.open(cacheName).then(function (cache) {
        cache.put(request, response.clone());
      });
    }
    return response;
  }).catch(function () {
    return caches.open(cacheName).then(function (cache) {
      return cache.match(request).then(function (cached) {
        return cached || caches.match('/');
      });
    });
  });
}

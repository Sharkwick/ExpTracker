const CACHE = "exptracker-shell-v1";
const SHELL = ["/", "/static/app.js", "/manifest.json", "/favicon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  if (!SHELL.includes(url.pathname)) return;
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request))
  );
});
/* Offline-Unterstützung: immer zuerst das Netz (damit Prognosen aktuell sind),
   ohne Verbindung der zuletzt geladene Stand. Nur Dateien dieser Seite. */
const CACHE = "prognose-v1";
const GRUNDGERUEST = ["./", "index.html", "style.css", "app.js", "manifest.webmanifest", "icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(GRUNDGERUEST)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((namen) => Promise.all(namen.filter((n) => n !== CACHE).map((n) => caches.delete(n))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;
  e.respondWith(
    fetch(e.request).then((antwort) => {
      if (antwort.ok) {
        const kopie = antwort.clone();
        caches.open(CACHE).then((c) => c.put(e.request, kopie));
      }
      return antwort;
    }).catch(() => caches.match(e.request, { ignoreSearch: true })
      .then((r) => r || caches.match("index.html")))
  );
});

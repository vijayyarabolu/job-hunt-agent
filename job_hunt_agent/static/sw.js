// App shell only, network first. Never job data or tokens: /api/* and ?query URLs bypass it.
const SHELL = "jha-shell-v1";

addEventListener("install", () => skipWaiting());
addEventListener("activate", (e) => e.waitUntil(caches.keys()
  .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
  .then(() => clients.claim())));
addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.search || url.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request).then((res) => {
    if (res.ok && res.type === "basic") {
      const copy = res.clone();
      caches.open(SHELL).then((c) => c.put(url.pathname, copy));
    }
    return res;
  }).catch(() => caches.match(url.pathname).then((hit) => hit || Response.error())));
});

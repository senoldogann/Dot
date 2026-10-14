// The service worker of the phone (and of any browser that installs Dot): the shell files are fetched from the
// network and kept as the fallback for when the Mac is out of reach, nothing under /api or /pages (the pages runs
// showed) is ever cached, and a push from the daemon becomes a notification whose tap opens the run.

const SHELL_CACHE = "dot-shell-v1";
const API_PREFIX = "/api";
const PAGES_PREFIX = "/pages/";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) => Promise.all(names.filter((name) => name !== SHELL_CACHE).map((name) => caches.delete(name))))
      .then(() => self.clients.claim()),
  );
});

/** Shell files: the network first, a copy kept for later; the copy when the network fails. */
async function shellFirst(request) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const fresh = await fetch(request);
    if (fresh.ok) {
      cache.put(request, fresh.clone());
    }
    return fresh;
  } catch (error) {
    const kept = await cache.match(request);
    if (kept !== undefined) {
      return kept;
    }
    throw error;
  }
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (
    event.request.method !== "GET" ||
    url.origin !== self.location.origin ||
    url.pathname.startsWith(API_PREFIX) ||
    url.pathname.startsWith(PAGES_PREFIX)
  ) {
    return;
  }
  event.respondWith(shellFirst(event.request));
});

self.addEventListener("push", (event) => {
  const message = event.data === null ? null : event.data.json();
  if (message === null) {
    return;
  }
  event.waitUntil(
    self.registration.showNotification(message.title, {
      body: message.body,
      icon: "icons/icon-192.png",
      badge: "icons/icon-192.png",
      tag: message.route,
      data: { route: message.route },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const route = typeof event.notification.data?.route === "string" ? event.notification.data.route : "#/activity";
  const target = new URL(`/${route}`, self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const open = windows.find((client) => "focus" in client);
      if (open !== undefined) {
        open.navigate(target);
        return open.focus();
      }
      return self.clients.openWindow(target);
    }),
  );
});

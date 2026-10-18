// The service worker of the phone (and of any browser that installs Dot): the shell files are fetched from the
// network and kept as the fallback for when the Mac is out of reach, nothing under /api or /pages (the pages runs
// showed) is ever cached, and a push from the daemon becomes a notification whose tap opens the run.
//
// A page opened while the phone's network wakes (item 328) does not wait for it: when the network has not answered
// within NETWORK_WAIT_MS, the kept copy answers, and every file of that page comes from the same copy; after a newer
// build was noted (item 359), an opening waits for the network up to NEWER_WAIT_MS instead. The copies are kept by the
// build of the pages they are of (the daemon names it in BUILD_HEADER, kept_pages.py), one build at a time, so a page
// is never half one build and half another; the page then compares its build with the daemon's (state.js).

const PAGES_CACHE = "dot-pages-";
// A cache named for a build the network named after a copy of another build had answered (item 359). It holds nothing:
// its name outlives this service worker, which iOS stops between openings, and tells the next opening to wait for the
// network, so that the page and its files come from that build and are kept whole. Until then the copy stays as it is.
const NEWER_CACHE = "dot-newer-";
const BUILD_HEADER = "X-Dot-Pages";
const NETWORK_WAIT_MS = 200;
// How long an opening waits for the network after a newer build was noted (item 359): long enough for a slow network to
// answer, short enough that a Mac out of reach, whose connections hang rather than fail, leaves the copy to answer.
const NEWER_WAIT_MS = 5000;
const API_PREFIX = "/api";
const PAGES_PREFIX = "/pages/";

// The pages answered from a kept copy, by their client's id: the cache whose files they take.
const keptClients = new Map();

self.addEventListener("install", () => {
  self.skipWaiting();
});

// The caches of an earlier service worker go (`dot-shell-v1` kept files of every build in one).
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((name) => !name.startsWith(PAGES_CACHE) && !name.startsWith(NEWER_CACHE))
            .map((name) => caches.delete(name)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

/** Keep a copy of a file the network answered, with the files of its build. */
async function keep(request, response) {
  const build = response.headers.get(BUILD_HEADER);
  if (!response.ok || build === null) {
    return;
  }
  const cache = await caches.open(`${PAGES_CACHE}${build}`);
  await cache.put(request, response);
}

/** Keep a copy of a page the network answered; the copies of every other build go, so the page's build is the one kept,
 * and so does the note of a newer build (NEWER_CACHE). */
async function keepPage(request, response) {
  const build = response.headers.get(BUILD_HEADER);
  await keep(request, response);
  if (build === null) {
    return;
  }
  const names = await caches.keys();
  await Promise.all(names.filter((name) => name !== `${PAGES_CACHE}${build}`).map((name) => caches.delete(name)));
}

/** The kept copy of `request` and the name of its cache, or null when none is kept. */
async function keptCopy(request) {
  for (const name of await caches.keys()) {
    const response = await (await caches.open(name)).match(request);
    if (response !== undefined) {
      return { name, response };
    }
  }
  return null;
}

/** Whether the network named a build after a copy of another had answered, which the next opening waits for up to
 * NEWER_WAIT_MS (item 359). */
async function newerNamed() {
  return (await caches.keys()).some((name) => name.startsWith(NEWER_CACHE));
}

/** Note the build `response` names when it is not the one of the copy `name`, which answered while the network woke. */
async function noteNewer(name, response) {
  const build = response === undefined ? null : response.headers.get(BUILD_HEADER);
  if (build !== null && `${PAGES_CACHE}${build}` !== name) {
    await caches.open(`${NEWER_CACHE}${build}`);
  }
}

/** A promise that resolves with `value` after `ms`. */
function after(ms, value) {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/**
 * A page opened: the network's answer when it comes within NETWORK_WAIT_MS, else the kept copy, whose files the page
 * then takes too; the kept copy also when the network fails, and the network's late answer when nothing is kept. A late
 * answer that names another build than the copy's is noted, and the next opening waits for the network up to
 * NEWER_WAIT_MS (item 359): otherwise a phone whose network is always slower than NETWORK_WAIT_MS would open the build
 * it first kept for ever.
 */
async function openPage(event) {
  const answer = fetch(event.request).then(
    (response) => ({ response }),
    (error) => ({ error }),
  );
  const wait = (await newerNamed()) ? NEWER_WAIT_MS : NETWORK_WAIT_MS;
  const early = await Promise.race([answer, after(wait, null)]);
  if (early !== null && early.response !== undefined) {
    keepPage(event.request, early.response.clone());
    return early.response;
  }
  const kept = await keptCopy(event.request);
  if (kept !== null) {
    keptClients.set(event.resultingClientId, kept.name);
    event.waitUntil(answer.then((late) => noteNewer(kept.name, late.response)));
    return kept.response;
  }
  const late = await answer;
  if (late.error !== undefined) {
    throw late.error;
  }
  keepPage(event.request, late.response.clone());
  return late.response;
}

/** A file of a page: from the copy the page was answered from, else the network first and a copy kept, the copy when the
 * network fails. */
async function shellFile(event) {
  const name = keptClients.get(event.clientId);
  if (name !== undefined) {
    const kept = await (await caches.open(name)).match(event.request);
    return kept ?? fetch(event.request);
  }
  try {
    const fresh = await fetch(event.request);
    keep(event.request, fresh.clone());
    return fresh;
  } catch (error) {
    const kept = await caches.match(event.request);
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
  event.respondWith(event.request.mode === "navigate" ? openPage(event) : shellFile(event));
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
  // A tap opens one of the page's own routes (`#/…`, as the daemon sends them); anything else opens the activity.
  const given = event.notification.data?.route;
  const route = typeof given === "string" && given.startsWith("#/") ? given : "#/activity";
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

// The first paint (item 81): a classic script in the document's head that puts the look, the colour scheme and the
// density this browser chose on the page element before the body is parsed, so a held dark scheme never flashes light
// while main.js loads. It reads the same keys as theme.js and layout.js and sets only values those modules accept;
// everything else (the defaults, the switches, the listeners) stays theirs. Storage that is blocked means the default.
(function boot() {
  var known = { "dot.theme": ["neutral", "paper"], "dot.scheme": ["light", "dark"], "dot.density": ["comfortable", "compact"] };
  var names = { "dot.theme": "theme", "dot.scheme": "scheme", "dot.density": "density" };
  Object.keys(known).forEach(function (key) {
    var stored = null;
    try {
      stored = localStorage.getItem(key);
    } catch (error) {
      stored = null;
    }
    if (stored !== null && known[key].indexOf(stored) !== -1) {
      document.documentElement.dataset[names[key]] = stored;
    }
  });
  // Inside the desktop shell (Tauri, item 88) the page keeps room for the traffic lights in its top bars.
  if (typeof window.__TAURI__ === "object" && window.__TAURI__ !== null) {
    document.documentElement.dataset.shell = "desktop";
  }
  // The opening (item 114, app.css): once per window session, so at every launch of the desktop shell and once per
  // tab, never when the system reduces motion, and not at all when the storage is blocked, since a page that cannot
  // remember it played would play it at every reload. The overlay's own last frame ends it; a click or a key first
  // turns it into a quick fade.
  var opening = false;
  try {
    opening =
      sessionStorage.getItem("dot.opened") === null && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (opening) {
      sessionStorage.setItem("dot.opened", "1");
    }
  } catch (error) {
    opening = false;
  }
  if (opening) {
    var root = document.documentElement;
    var skip = function () {
      if (root.dataset.opening === "on") {
        root.dataset.opening = "skip";
      }
    };
    var end = function (event) {
      if (!(event.target instanceof Element) || !event.target.classList.contains("opening")) {
        return;
      }
      delete root.dataset.opening;
      document.removeEventListener("animationend", end);
      window.removeEventListener("pointerdown", skip, true);
      window.removeEventListener("keydown", skip, true);
    };
    root.dataset.opening = "on";
    document.addEventListener("animationend", end);
    window.addEventListener("pointerdown", skip, true);
    window.addEventListener("keydown", skip, true);
  }
})();

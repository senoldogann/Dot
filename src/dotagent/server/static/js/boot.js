// The first paint (item 81): a classic script in the document's head that puts the theme, the colour scheme, the
// density and the settings of the interface, the motion and the type this browser chose on the page element before
// the body is parsed, so a held dark scheme or a theme never flashes the default while main.js loads. It reads the same
// keys as theme.js and layout.js and sets only values of the shapes those modules write, and paints the settings of
// Settings › Appearance with appearance/rules.js, which runs before it; everything else (the defaults, the switches,
// the listeners) stays theirs, and theme.js works the tokens out again once it runs. Storage that is blocked means the default.
(function boot() {
  var root = document.documentElement;
  var read = function (key) {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  };
  var scheme = read("dot.scheme");
  if (scheme === "light" || scheme === "dark") {
    root.dataset.scheme = scheme;
  }
  var density = read("dot.density");
  if (density === "comfortable" || density === "compact") {
    root.dataset.density = density;
  }
  var work = read("dot.work");
  if (work === "steps" || work === "status") {
    root.dataset.work = work;
  }
  // The theme of the scheme on screen (item 126): the pick for dark while the page is dark, else the pick for light.
  var themeId = /^[a-z0-9][a-z0-9-]{0,47}$/;
  var light = read("dot.theme");
  light = light !== null && themeId.test(light) ? light : null;
  var dark = read("dot.themeDark");
  dark = dark !== null && themeId.test(dark) ? dark : light;
  var shown = root.dataset.scheme || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  var theme = shown === "dark" ? dark : light;
  if (theme !== null) {
    root.dataset.theme = theme;
  }
  // The tokens theme.js wrote for the picks, each a light-dark() pair of two plain colours; anything else is left out.
  var side = "(#[0-9a-f]{3,8}|(rgb|oklch)\\([0-9a-z .%/,-]*\\)|transparent)";
  var pair = new RegExp("^light-dark\\(" + side + ", " + side + "\\)$", "i");
  var tokens = null;
  try {
    tokens = JSON.parse(read("dot.themeTokens") || "null");
  } catch {
    tokens = null;
  }
  if (tokens !== null && typeof tokens === "object") {
    Object.keys(tokens).forEach(function (token) {
      if (/^--[a-z0-9-]+$/.test(token) && typeof tokens[token] === "string" && pair.test(tokens[token])) {
        root.style.setProperty(token, tokens[token]);
      }
    });
  }
  // The interface, the motion and the type (items 127, 331 and 1062): each kept value as appearance/rules.js paints it,
  // the rules appearance/settings.js keeps and paints a choice with (item 1067: one copy of them, not two).
  globalThis.DOT_APPEARANCE.paintAll(read, root);
  // Inside the desktop shell (Tauri, item 88) the page keeps room for the traffic lights in its top bars.
  if (typeof window.__TAURI__ === "object" && window.__TAURI__ !== null) {
    document.documentElement.dataset.shell = "desktop";
  }
  // iOS zooms the page into a text field whose text is under 16 px as the field gets the focus (item 273), but not past
  // the largest scale the viewport allows. So on an iPhone and an iPad the viewport allows no scale past the first and
  // the fields keep the size they have on the Mac (item 311); app.css gives a field at least 16 px on the other touch
  // screens. An iPad that asks for the desktop's pages says it is a Mac with a touch screen.
  var ios =
    /\b(iPhone|iPad|iPod)\b/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var viewport = document.querySelector('meta[name="viewport"]');
  if (ios && viewport !== null) {
    viewport.setAttribute("content", viewport.getAttribute("content") + ", maximum-scale=1");
    root.dataset.focusZoom = "off";
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
  } catch {
    opening = false;
  }
  if (opening) {
    // Once the overlay has begun to fade, the quick fade would only start again from full and flash (item 116).
    const skip = function () {
      var overlay = document.querySelector(".opening");
      if (root.dataset.opening === "on" && overlay !== null && getComputedStyle(overlay).opacity === "1") {
        root.dataset.opening = "skip";
      }
    };
    const end = function (event) {
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

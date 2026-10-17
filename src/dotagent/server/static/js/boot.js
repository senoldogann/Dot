// The first paint (item 81): a classic script in the document's head that puts the theme, the colour scheme, the
// density and the settings of the interface, the motion and the type this browser chose on the page element before
// the body is parsed, so a held dark scheme or a theme never flashes the default while main.js loads. It reads the same
// keys as theme.js, layout.js and appearance/settings.js and sets only values of the shapes those modules write;
// everything else (the defaults, the switches, the listeners) stays theirs, and theme.js works the tokens out again
// once it runs. Storage that is blocked means the default.
(function boot() {
  var root = document.documentElement;
  var read = function (key) {
    try {
      return localStorage.getItem(key);
    } catch (error) {
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
  } catch (error) {
    tokens = null;
  }
  if (tokens !== null && typeof tokens === "object") {
    Object.keys(tokens).forEach(function (token) {
      if (/^--[a-z0-9-]+$/.test(token) && typeof tokens[token] === "string" && pair.test(tokens[token])) {
        root.style.setProperty(token, tokens[token]);
      }
    });
  }
  // The interface, the motion and the type (item 127): for each value kept, what appearance/settings.js writes for it,
  // and nothing for the default or for a value it would not keep.
  var whole = function (key, least, most, step) {
    var stored = read(key);
    if (stored === null || !/^\d{1,3}$/.test(stored)) {
      return null;
    }
    var value = Number(stored);
    return value >= least && value <= most && (value - least) % step === 0 ? value : null;
  };
  var contrast = whole("dot.contrast", 50, 200, 5);
  if (contrast !== null && contrast !== 100) {
    root.setAttribute("data-contrast", "");
    root.style.setProperty("--contrast-base", Math.min(contrast, 100) + "%");
    root.style.setProperty("--contrast-boost", Math.max(contrast - 100, 0) + "%");
    root.style.setProperty("--contrast-border-boost", Math.max(contrast - 100, 0) / 4 + "%");
  }
  var chatWidth = read("dot.chatWidth");
  if (chatWidth === "wide" || chatWidth === "full") {
    root.setAttribute("data-chat-width", chatWidth);
  }
  var panelMotion = whole("dot.panelMotion", 0, 400, 25);
  if (panelMotion !== null && panelMotion !== 200) {
    root.style.setProperty("--motion-panel", panelMotion + "ms");
  }
  // The body's size on this device, as appearance/settings.js keeps it (item 331): on a phone (app.css, item 329) 14 to
  // 20 px, 16 its own; elsewhere 13 to 17 px, 14 its own.
  var phoneType = window.matchMedia(
    "(pointer: coarse) and (max-width: 720px), (pointer: coarse) and (max-height: 500px)"
  ).matches;
  var ownBody = phoneType ? 16 : 14;
  var interfaceSize = phoneType ? whole("dot.sizeInterface", 14, 20, 1) : whole("dot.sizeInterface", 13, 17, 1);
  if (interfaceSize !== null && interfaceSize !== ownBody) {
    root.style.setProperty("--type-scale", String(interfaceSize / ownBody));
  }
  var answers = read("dot.fontAnswers");
  if (answers === "interface" || answers === "serif") {
    root.style.setProperty("--voice", answers === "interface" ? "var(--font)" : "var(--family-serif)");
  }
  [
    ["dot.sizeComposer", "--composer-size", 12, 20, 15],
    ["dot.sizeCode", "--code-size", 10, 18, 13],
  ].forEach(function (size) {
    var value = whole(size[0], size[2], size[3], 1);
    if (value !== null && value !== size[4]) {
      root.style.setProperty(size[1], value + "px");
    }
  });
  var typed = /^[\p{L}\p{N} ,'"._-]{1,200}$/u;
  var quoted = function (name) {
    var bare = name.split('"').join("").split("'").join("").trim();
    var plain =
      /^[a-zA-Z][a-zA-Z0-9-]*$/.test(bare) && !/^(inherit|initial|unset|revert|revert-layer|default)$/i.test(bare);
    return bare === "" || plain ? bare : '"' + bare + '"';
  };
  [
    ["dot.fontInterface", "--font", ["system"], "var(--family-inter)"],
    ["dot.fontComposer", "--composer-font", ["serif", "mono"], "var(--font)"],
    ["dot.fontCode", "--mono", ["sf-mono", "menlo"], "var(--family-geist-mono)"],
  ].forEach(function (font) {
    var stored = read(font[0]);
    if (stored === null) {
      return;
    }
    if (font[2].indexOf(stored) >= 0) {
      root.style.setProperty(font[1], "var(--family-" + stored + ")");
      return;
    }
    var families = typed.test(stored)
      ? stored
          .split(",")
          .map(quoted)
          .filter(function (name) {
            return name !== "";
          })
      : [];
    if (families.length > 0) {
      root.style.setProperty(font[1], families.join(", ") + ", " + font[3]);
    }
  });
  if (read("dot.smoothing") === "off") {
    root.setAttribute("data-smoothing", "off");
  }
  if (read("dot.wrapCode") === "on") {
    root.setAttribute("data-wrap-code", "");
  }
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
  } catch (error) {
    opening = false;
  }
  if (opening) {
    // Once the overlay has begun to fade, the quick fade would only start again from full and flash (item 116).
    var skip = function () {
      var overlay = document.querySelector(".opening");
      if (root.dataset.opening === "on" && overlay !== null && getComputedStyle(overlay).opacity === "1") {
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

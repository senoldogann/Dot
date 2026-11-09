// The rules of the settings of Settings › Appearance that paint the page (items 127, 331 and 1062), in one place (item
// 1067): for each, the key it is kept under in this browser, its default, how a kept value is read (what it would not
// keep reads as none) and how a value is put on the page element: an attribute where the stylesheets switch on one, a
// token on the element's style where a value is carried, and nothing at the default. A family is one of the stacks
// app.css names (`--family-<id>`) or families typed, which go before the face's own, so that no glyph goes missing.
//
// A classic script, loaded before boot.js and main.js: boot.js paints what is kept before the first paint with these
// rules, and appearance/settings.js keeps and paints a choice with the same ones once the page runs. Before item 1067
// each held its own copy of them, which drifted twice in a day (the families of item 1062 and a kept size of the
// answers). In part derived code (see LICENSE in this folder).
(function rules() {
  // What a family typed may hold: letters, digits, spaces, commas between families, quotes, dots, dashes, underscores.
  const TYPED_FAMILIES = /^[\p{L}\p{N} ,'"._-]{1,200}$/u;
  // The names CSS reads as keywords in a list of families, quoted when typed as a family.
  const KEYWORDS = /^(inherit|initial|unset|revert|revert-layer|default)$/i;
  // The families each of the four faces offers (item 1062), ids of app.css's `--family-<id>`; the answers and the
  // message box, which follow the interface or the code, offer theirs too (`--family-interface`, `--family-mono`), which
  // the interface and the code cannot take of each other (a circle).
  const FAMILY_STACKS = ["system", "inter", "serif", "geist-mono", "sf-mono", "menlo"];
  const FOLLOWING_STACKS = ["interface", "mono", ...FAMILY_STACKS];
  // The weights each face offers (item 1062); 400 is each one's own.
  const WEIGHTS = [
    { id: 300, label: "Light" },
    { id: 400, label: "Regular" },
    { id: 500, label: "Medium" },
    { id: 600, label: "Semibold" },
  ];
  // A phone, as app.css draws one (item 329): its body is iOS's 16 px, down to 12 (item 347), a message 16; elsewhere
  // the body is Dot's 14, 13 to 17, a message 15.
  const PHONE = window.matchMedia(
    "(pointer: coarse) and (max-width: 720px), (pointer: coarse) and (max-height: 500px)",
  ).matches;
  const BODY = PHONE ? { own: 16, least: 12, most: 20, message: 16 } : { own: 14, least: 13, most: 17, message: 15 };

  /** A whole number between `least` and `most` on steps of `step` from `least`, read from its digits; else null. */
  const wholeIn = (least, most, step) => (stored) => {
    if (stored === null || !/^\d{1,3}$/.test(stored)) {
      return null;
    }
    const value = Number(stored);
    return value >= least && value <= most && (value - least) % step === 0 ? value : null;
  };
  const oneOf = (ids) => (stored) => (ids.includes(stored) ? stored : null);

  /** One family typed, as CSS reads it: a plain name as it is, anything else in double quotes (the typed quotes are
   * dropped, so that the quotes written always close). */
  const quoted = (name) => {
    const bare = name.replaceAll('"', "").replaceAll("'", "").trim();
    return bare === "" || (/^[a-zA-Z][a-zA-Z0-9-]*$/.test(bare) && !KEYWORDS.test(bare)) ? bare : `"${bare}"`;
  };
  /** The families typed as a CSS list, or null when nothing is typed. */
  const familyList = (typed) => {
    const families = typed.split(",").map(quoted).filter((name) => name !== "");
    return families.length > 0 ? families.join(", ") : null;
  };

  const token = (root, name, value) => (value === null ? root.style.removeProperty(name) : root.style.setProperty(name, value));
  const flag = (root, name, value) => (value === null ? root.removeAttribute(name) : root.setAttribute(name, value));

  /** A face's family (item 1062): "" for its own, one of `stacks`, or families typed, written before `fallback`; what a
   * browser kept before item 1062 offered every face the same families is read by `legacy` as what it is now. */
  const family = (key, name, stacks, fallback, legacy) => ({
    key,
    initial: () => "",
    stacks,
    parse: (stored) => {
      if (stored === null) {
        return null;
      }
      if (Object.hasOwn(legacy, stored)) {
        return legacy[stored];
      }
      return stacks.includes(stored) || (TYPED_FAMILIES.test(stored) && familyList(stored) !== null) ? stored : null;
    },
    paint: (value, _kept, root) =>
      token(root, name, value === "" ? null : stacks.includes(value) ? `var(--family-${value})` : `${familyList(value)}, ${fallback}`),
  });
  /** A size in pixels on its own token, nothing written at `initial`. */
  const size = (key, name, least, most, initial) => ({
    key,
    initial: () => initial,
    least,
    most,
    parse: wholeIn(least, most, 1),
    paint: (value, _kept, root) => token(root, name, value === initial ? null : `${value}px`),
  });
  /** A weight on its own token, nothing written at 400. */
  const weight = (key, name) => ({
    key,
    initial: () => 400,
    parse: (stored) => WEIGHTS.find((option) => String(option.id) === stored)?.id ?? null,
    paint: (value, _kept, root) => token(root, name, value === 400 ? null : String(value)),
  });
  /** An on/off switch drawn as an attribute: `on` writes `written` under `name`, "off" or the default nothing. */
  const switched = (key, initial, name, onValue, written) => ({
    key,
    initial: () => initial,
    parse: oneOf(["on", "off"]),
    paint: (value, _kept, root) => flag(root, name, value === onValue ? written : null),
  });

  const interfaceSize = {
    key: "dot.sizeInterface",
    initial: () => BODY.own,
    least: BODY.least,
    most: BODY.most,
    parse: wholeIn(BODY.least, BODY.most, 1),
    paint: (value, _kept, root) => token(root, "--type-scale", value === BODY.own ? null : String(value / BODY.own)),
  };

  const RULES = {
    /** The contrast in percent, 50 to 200 on steps of 5: below 100 the text and the lines fade, above it they deepen. */
    contrast: {
      key: "dot.contrast",
      initial: () => 100,
      parse: wholeIn(50, 200, 5),
      paint: (value, _kept, root) => {
        const own = value === 100;
        flag(root, "data-contrast", own ? null : "");
        token(root, "--contrast-base", own ? null : `${Math.min(value, 100)}%`);
        token(root, "--contrast-boost", own ? null : `${Math.max(value - 100, 0)}%`);
        token(root, "--contrast-border-boost", own ? null : `${Math.max(value - 100, 0) / 4}%`);
      },
    },
    /** The reading width of the home and the conversation: 768 px, 1152 px or the window's. */
    chatWidth: {
      key: "dot.chatWidth",
      initial: () => "comfortable",
      parse: oneOf(["comfortable", "wide", "full"]),
      paint: (value, _kept, root) => flag(root, "data-chat-width", value === "comfortable" ? null : value),
    },
    /** How long the sidebar and the details panel slide, 0 to 400 ms on steps of 25. */
    panelMotion: {
      key: "dot.panelMotion",
      initial: () => 200,
      parse: wholeIn(0, 400, 25),
      paint: (value, _kept, root) => token(root, "--motion-panel", value === 200 ? null : `${value}ms`),
    },
    // The interface's face: the system's by default (item 1057).
    interfaceFont: family("dot.fontInterface", "--font", FAMILY_STACKS, "var(--family-system)", {}),
    /** The size of the body's text on this device (item 331), which the seven sizes of the type are scaled by over the
     * device's own; kept in this browser, so that each device has its own. */
    interfaceSize,
    interfaceWeight: weight("dot.weightInterface", "--weight-text"),
    /** The answers' face: the theme's (serif in Paper) by default. Before item 1062 "theme" was kept for it. */
    answerFont: family("dot.fontAnswers", "--voice", FOLLOWING_STACKS, "var(--theme-voice)", { theme: "" }),
    /** The answers' size (item 1062): a message's size scaled with the interface's until set, then its own, 12 to 24 px;
     * the line keeps the ratio of a message's. Its default moves with the interface's, so it is painted by whether a
     * size is kept (`kept`), not by what the default is now: a size kept stays when the default comes to it. */
    answerSize: {
      key: "dot.sizeAnswers",
      initial: (read) => Math.round((BODY.message * (interfaceSize.parse(read(interfaceSize.key)) ?? BODY.own)) / BODY.own),
      least: 12,
      most: 24,
      parse: wholeIn(12, 24, 1),
      paint: (value, kept, root) => token(root, "--answer-size", kept ? `${value}px` : null),
    },
    answerWeight: weight("dot.weightAnswers", "--answer-weight"),
    /** The message box's face: the interface's by default. */
    composerFont: family("dot.fontComposer", "--composer-font", FOLLOWING_STACKS, "var(--font)", {}),
    composerSize: size("dot.sizeComposer", "--composer-size", 12, 20, 15),
    composerWeight: weight("dot.weightComposer", "--composer-weight"),
    /** The code's face: Geist Mono by default. */
    codeFont: family("dot.fontCode", "--mono", FAMILY_STACKS, "var(--family-geist-mono)", {}),
    codeSize: size("dot.sizeCode", "--code-size", 10, 18, 13),
    codeWeight: weight("dot.weightCode", "--code-weight"),
    /** Font smoothing, "on" (antialiased, Dot's) or "off" (the system's heavier strokes on a Mac). */
    smoothing: switched("dot.smoothing", "on", "data-smoothing", "off", "off"),
    /** Long lines of code in an answer, "off" (scrolled sideways, Dot's) or "on" (wrapped). */
    wrapCode: switched("dot.wrapCode", "off", "data-wrap-code", "on", ""),
  };

  /** Put each rule's kept value, or its default, on `root`, reading the kept values with `read(key)`. */
  const paintAll = (read, root) => {
    for (const rule of Object.values(RULES)) {
      const kept = rule.parse(read(rule.key));
      rule.paint(kept ?? rule.initial(read), kept !== null, root);
    }
  };

  globalThis.DOT_APPEARANCE = Object.freeze({ rules: RULES, weights: WEIGHTS, paintAll });
})();

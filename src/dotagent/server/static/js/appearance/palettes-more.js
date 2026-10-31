// More palettes (beyond the six of palettes.js): Midnight, Nord, Solarized, Sepia, Forest, Graphite and High
// contrast, for light and for dark, in the roles palettes.js names. Dot's own; tokens.js holds them to the contrast
// rule like the rest.

// A palette's roles from its colours in the order of ROLES; the placeholder and the muted icons take the muted text,
// the send button the accent.
const ROLES = ["canvas", "sidebar", "surface", "accentSurface", "border", "input", "text", "textMuted", "accent", "hover", "onAccent", "update", "msg", "msgFg", "error", "errorFg", "errorSurface", "warning", "warningFg", "warningSurface"];

function roles(colours) {
  const c = Object.fromEntries(ROLES.map((role, index) => [role, colours.split(" ")[index]]));
  return {
    canvas: c.canvas,
    sidebar: c.sidebar,
    surface: c.surface,
    accentSurface: c.accentSurface,
    border: c.border,
    input: c.input,
    text: c.text,
    textMuted: c.textMuted,
    placeholder: c.textMuted,
    iconMuted: c.textMuted,
    accent: c.accent,
    messageAction: c.accent,
    messageActionHover: c.hover,
    messageActionForeground: c.onAccent,
    updateSurface: c.update,
    messageSurface: c.msg,
    messageForeground: c.msgFg,
    error: c.error,
    errorForeground: c.errorFg,
    errorSurface: c.errorSurface,
    warning: c.warning,
    warningForeground: c.warningFg,
    warningSurface: c.warningSurface,
  };
}

export const MORE_PALETTES = {
  midnight: {
    light: roles("#f5f8fc #edf2f9 #ffffff #e6edf7 #d9e2ef #c5d2e4 #14213d #556179 #2457c5 #1d48a6 #ffffff #dfe8f8 #e6edf7 #14213d #e5484d #b4232a #fbeaea #f5a524 #9a5b00 #fdf3e1"),
    dark: roles("#0b1220 #080e1a #111a2e #18233b #1c2740 #26324d #e6ecf7 #8e9ab3 #5b8cff #477bf0 #0b1220 #16223f #18233b #e6ecf7 #ff5c61 #ff8a8e #33161a #ffb547 #ffc56b #33260f"),
  },
  nord: {
    light: roles("#eceff4 #e5e9f0 #f6f8fb #e0e5ed #d8dee9 #c4ccda #2e3440 #4c566a #4c6d96 #3e5a7d #ffffff #dce5f0 #e5e9f0 #2e3440 #bf616a #a3434c #f4e4e6 #d08770 #9a5233 #f6ebe4"),
    dark: roles("#2e3440 #272c36 #3b4252 #434c5e #434c5e #4c566a #eceff4 #a3abbb #88c0d0 #81a1c1 #2e3440 #3b4a5c #434c5e #eceff4 #bf616a #e0868e #45343a #ebcb8b #ebcb8b #463f33"),
  },
  solarized: {
    light: roles("#fdf6e3 #eee8d5 #fffbef #eee8d5 #e4dcc4 #d3cbb3 #073642 #586e75 #1f6fa8 #195a89 #ffffff #e3ecdf #eee8d5 #073642 #dc322f #b02623 #f8e4d6 #b58900 #8a6600 #f6ecc9"),
    dark: roles("#002b36 #00222b #073642 #0c3f4d #0e4352 #1a5160 #eee8d5 #93a1a1 #3ba0e6 #268bd2 #002b36 #0b3d55 #0c3f4d #eee8d5 #dc322f #f0605c #3a2228 #b58900 #d9a70f #2d3418"),
  },
  sepia: {
    light: roles("#f4ecd8 #ece2ca #faf4e6 #e8dcc0 #ddd0b2 #cbbb98 #3b2f22 #6e5c45 #8b5a2b #744921 #ffffff #eadcc2 #e8dcc0 #3b2f22 #c0392b #9c2a1f #f6e0d6 #c98a1a #8a5a00 #f5e7c8"),
    dark: roles("#211b14 #1a1510 #2a231a #342b20 #3a3024 #4a3d2e #efe4cf #b3a185 #d39a5e #c08549 #211b14 #3d2f20 #342b20 #efe4cf #e06b5b #f08f80 #3c211b #e0a63f #ebbc63 #3a2d14"),
  },
  forest: {
    light: roles("#f3f6f1 #eaf0e7 #fbfdfa #e3ebdf #d6e0d1 #c0cebb #1d2a1f #546456 #2f7d4a #25663c #ffffff #dcebdf #e3ebdf #1d2a1f #d64545 #a83232 #f8e7e5 #d9931c #8f5d00 #f8eeda"),
    dark: roles("#111a14 #0c140f #17221a #1e2b21 #223026 #2d3d31 #e3ede4 #8fa392 #5cc489 #4bb078 #0c140f #1c3526 #1e2b21 #e3ede4 #f06767 #ff8f8f #3a1b1b #f0b24a #f5c56e #3a2e14"),
  },
  // Grok's greys as OpenMausBot sampled them from the app (item 870): layers of neutral grey, the ink at three
  // strengths, a white action, and colour only for what needs the owner.
  graphite: {
    light: roles("#ffffff #f7f7f7 #f2f2f2 #e9e9eb #e3e3e3 #cfcfcf #0d0d0d #5c5c5c #0d0d0d #2c2c2c #ffffff #eef2fb #f0f0f0 #0d0d0d #d92d20 #b42318 #fdecea #c27803 #8a5200 #fdf3e1"),
    dark: roles("#121212 #0e0e0e #1f1f1f #2c2c2e #2a2a2a #3a3a3c #f5f5f5 #a3a3a3 #f5f5f5 #e0e0e0 #0e0e0e #1c2433 #2c2c2e #f5f5f5 #ff5c5c #ff8a8a #2e1414 #f5a524 #ffc266 #2e2210"),
  },
  contrast: {
    light: roles("#ffffff #ffffff #ffffff #ebebeb #000000 #000000 #000000 #1f1f1f #0033cc #002299 #ffffff #dde5ff #ebebeb #000000 #cc0000 #a30000 #ffe5e5 #b36b00 #7a4300 #fff2db"),
    dark: roles("#000000 #000000 #0a0a0a #1f1f1f #8a8a8a #ffffff #ffffff #e0e0e0 #ffd400 #ffe14d #000000 #2e2a00 #1f1f1f #ffffff #ff4d4d #ff8080 #330000 #ffb000 #ffc94d #332300"),
  },
};

// The words of each palette above, for its card: [id, label, hint].
export const MORE_THEMES = [
  ["midnight", "Midnight", "Ice blue days and deep navy nights, a cobalt accent."],
  ["nord", "Nord", "Arctic snow and polar night, a frost-blue accent."],
  ["solarized", "Solarized", "Schoonover's base tones, a calm blue accent."],
  ["sepia", "Sepia", "Old paper and coffee, a sepia-brown accent."],
  ["forest", "Forest", "Moss and pine, a leaf-green accent."],
  ["graphite", "Graphite", "Grok's neutral greys, white ink and a white action."],
  ["contrast", "High contrast", "Pure black and white, bold edges, a strong blue."],
];

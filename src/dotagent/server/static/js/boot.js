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
})();

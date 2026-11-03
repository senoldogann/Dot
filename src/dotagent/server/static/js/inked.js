// A shape of Dot's own SVG files drawn inline in the ink of where it stands (item 959): the logos of services
// (`static/icons/plugins`, marks.js) and the marks of model providers (`static/icons/providers`, providers.js). Drawn as
// a CSS mask of the file, as they were, WebKit (Dot.app's engine) drew the file anew on every repaint around it, a key
// typed in the composer, which grows, among them, and the marks blinked. Inline, a mark is part of the page.

/**
 * A file being read, or read (`shape`: its `<svg>` to clone, `null` when the file is no SVG, `undefined` not yet).
 * @typedef {{ shape: Element | null | undefined, done: Promise<void> }} Reading
 */

/** @type {Map<string, Reading>} */
const READ = new Map();
const SVG_NS = "http://www.w3.org/2000/svg";
// The elements a shape keeps: what draws a shape and its paint, nothing that runs, animates, reaches out or styles.
const KEPT = new Set(["svg", "g", "path", "circle", "ellipse", "rect", "polygon", "polyline", "line", "defs", "linearGradient", "radialGradient", "stop"]);
// The paint a shape keeps: none stays none, every other is the ink of where it stands.
const PAINTS = ["fill", "stroke"];

/**
 * The shape of the SVG text `text` in one colour: the elements of `KEPT` alone, with no attribute that runs or reaches
 * out, every paint the ink it stands on (`currentColor`), sized by its holder. `null` when it is no SVG.
 * @param {string} text
 * @returns {Element | null}
 */
function inkedShape(text) {
  const svg = new DOMParser().parseFromString(text, "image/svg+xml").documentElement;
  if (svg.namespaceURI !== SVG_NS || svg.localName !== "svg") {
    return null;
  }
  for (const node of [...svg.querySelectorAll("*")]) {
    if (!KEPT.has(node.localName) || node.namespaceURI !== SVG_NS) {
      node.remove();
    }
  }
  for (const node of [svg, ...svg.querySelectorAll("*")]) {
    for (const name of node.getAttributeNames()) {
      if (name.startsWith("on") || name.endsWith("href") || name === "style" || name === "class") {
        node.removeAttribute(name);
      }
    }
    for (const paint of PAINTS) {
      const value = node.getAttribute(paint);
      if (value !== null && value !== "none") {
        node.setAttribute(paint, "currentColor");
      }
    }
  }
  svg.setAttribute("fill", "currentColor");
  svg.removeAttribute("width");
  svg.removeAttribute("height");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  return svg;
}

/**
 * The file at `path` as read: read once a page, and again after a read that failed.
 * @param {string} path
 * @returns {Reading}
 */
function read(path) {
  const known = READ.get(path);
  if (known !== undefined) {
    return known;
  }
  /** @type {Reading} */
  const entry = { shape: undefined, done: Promise.resolve() };
  entry.done = fetch(path)
    .then((response) => {
      if (!response.ok) {
        throw new Error(`${path} answered ${response.status}`);
      }
      return response.text();
    })
    .then((text) => {
      entry.shape = inkedShape(text);
      if (entry.shape === null) {
        console.warn("a mark's file is no SVG", path);
      }
    })
    .catch((error) => {
      READ.delete(path);
      console.warn("a mark's file could not be read", path, error);
    });
  READ.set(path, entry);
  return entry;
}

/**
 * Draw the SVG file at `path` inside `holder`, in its ink: at once when it was read before, else once it is.
 * @param {Element} holder
 * @param {string} path
 * @returns {Element}
 */
export function drawInked(holder, path) {
  const reading = read(path);
  if (reading.shape !== undefined) {
    if (reading.shape !== null) {
      holder.append(reading.shape.cloneNode(true));
    }
    return holder;
  }
  reading.done.then(() => {
    if (reading.shape) {
      holder.append(reading.shape.cloneNode(true));
    }
  });
  return holder;
}

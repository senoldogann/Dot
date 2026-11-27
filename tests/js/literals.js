// Finds the words of the interface that are written in the code instead of the catalogue (lang/en*.js and lang/tr*.js).
//
// A string literal or a template literal in a place that reaches the screen (a child of `el(...)`, `textContent`, a
// `title`, a label, a toast, a note, ...) and reads as English prose is "hard-coded": it shows the same in every language.
// tests/js/i18n-literals.test.js fails for each one that is not named in tests/js/i18n-exempt.json, so that a new line of
// the interface goes through `t()` and has its Turkish before it is merged. The scan reads the code with acorn and
// never runs it.

import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";

import { parse } from "acorn";

/** The words of the interface this test reads. */
export const STATIC_ROOT = new URL("../../src/dotagent/server/static/js/", import.meta.url).pathname;

// Calls whose strings are for the program: events, selectors, requests, logs, errors, collections.
const NOT_FOR_THE_SCREEN =
  /^(Error|TypeError|RangeError|console\..*|.*addEventListener|.*removeEventListener|.*classList\..*|.*querySelector.*|.*closest|.*matches|api\..*|request|icon|svg|Set|Map|writePref|readPref|ctx\.navigate|window\.matchMedia|.*style\.setProperty|.*getAttribute|.*removeAttribute|.*hasAttribute|Object\..*|JSON\..*|ThemeFileError|.*\.test|.*\.includes|.*\.startsWith|.*\.endsWith|.*\.indexOf|.*\.split|.*\.join|.*\.padStart|.*\.padEnd|.*\.repeat|.*\.replace|.*\.replaceAll|.*\.localeCompare|.*\.has|.*\.get|.*\.set|.*\.delete|.*\.add|.*\.getElementById|.*\.createElement|.*\.createElementNS|.*\.dispatchEvent|CustomEvent|Event|URL|URLSearchParams|RegExp|fetch|navigate|.*\.open|.*\.setItem|.*\.getItem|.*\.removeItem|.*\.setProperty|.*\.getPropertyValue|.*\.format|.*\.toLocale.*|Intl\..*|.*\.postMessage|.*\.focus|.*\.scrollIntoView|.*\.scrollTo|.*\.sendBeacon|.*\.then|.*\.catch|.*\.pushState|.*\.replaceState|.*\.toggleAttribute|.*\.contains)$/;

// Properties that are for the program: styles, roles, links, keys, kinds, names of things.
const NOT_TEXT_PROPS = new Set(
  "class className type id role href key kind tone tab icon value path state target rel size block autocomplete spellcheck for method inputmode accept pattern src srcset width height viewBox d fill stroke cx cy r x y transform points min max step dataset loading decoding draggable tabindex tabIndex hidden mode scheme family token tokens colour color bg fg accent view page section group scope status phase source mime format unit slot as cls event field code lang locale shape from to".split(
    " ",
  ),
);
const TEXT_PROPS = new Set(
  "title label text hint placeholder aria-label description message line lead summary heading confirmLabel submitLabel verb tooltip note empty caption subtitle name".split(
    " ",
  ),
);
// Calls that put their strings on the screen after the first argument.
const SCREEN_CALLS = /^(toast|setText|setLead|emptyPanel|note|field|section|card|row|fact|entry|step|offer|tile|chip|confirmDialog|piece|part)$/;
const TEXT_ASSIGNS = new Set(["textContent", "title", "placeholder", "alt", "innerText"]);
const ARIA_ATTRIBUTES = new Set(["aria-label", "title", "placeholder", "alt", "aria-description"]);
// Words that name a tone, an icon or a state, never a sentence.
const TONES = new Set("ok error info warn bad good muted primary danger ghost solid sm lg md true false none auto on off".split(" "));

/** The text of a template literal with its `${...}` places left empty. */
function bare(text) {
  return text.replace(/\$\{[^}]*\}/g, "").trim();
}

/** Whether `text` reads as a line of English written for a person, wherever it stands. */
function readsAsProse(text) {
  const plain = text.trim();
  const letters = bare(plain);
  if (!/[A-Za-z]{3,}/.test(letters)) {
    return false;
  }
  if (/^(--|#|\.[a-z-]+$|\/|\[|[a-z-]+\(|[a-z-]+:[a-z-])/.test(plain)) {
    return false;
  }
  if (/[{}=<>]|=>|::|\bhttps?:|\.(js|css|png|svg|json|md)\b|\bvar\(|rgba?\(|hsl|oklch\(|#[0-9a-f]{3,8}\b|\bpx$/.test(plain.replace(/\$\{[^}]*\}/g, ""))) {
    return false;
  }
  const marked = plain.replace(/\$\{[^}]*\}/g, "X");
  if (marked.split(/\s+/).filter((word) => /[A-Za-z]{2,}/.test(word)).length >= 2) {
    return true;
  }
  // "decides ${count}": a word and a value is a line too, where it has a space (a class name has none).
  if (plain.includes("${") && /\s/.test(plain) && /[A-Za-z]{3,}/.test(letters)) {
    return true;
  }
  return /^[A-Z][a-z]{2,}[.…:!?]?$/.test(marked) || /^[A-Z][a-z]+(-[a-z]+)+[.…:!?]?$/.test(marked);
}

/** Whether `text` is a word or line of the interface wherever it stands where only text goes. */
function isWords(text) {
  const letters = bare(text);
  if (!/[A-Za-z]{2,}/.test(letters)) {
    return false;
  }
  if (/^(--|\.|\/|https?:)|oklch\(|^[A-Za-z0-9_.-]+\.[a-z]{2,4}$|^\[agent\]|^dotx /.test(letters)) {
    return false;
  }
  return !(/^[A-Za-z0-9_]+$/.test(letters) && TONES.has(letters));
}

/** The calls' names as the scan reads them: `el`, `toast`, `api.get`, `classList.add`. */
function calleeName(callee) {
  if (callee.type === "Identifier") {
    return callee.name;
  }
  if (callee.type === "MemberExpression" && !callee.computed) {
    const owner = callee.object.type === "Identifier" ? `${callee.object.name}.` : callee.object.type === "MemberExpression" && !callee.object.computed ? `${callee.object.property.name}.` : "";
    return `${owner}${callee.property.name}`;
  }
  return "?";
}

/** What a literal is given to, climbing through the nodes that only pass it on (a condition, a list, a `??`). */
function consumer(node, parents) {
  let child = node;
  for (let at = parents.length - 1; at >= 0; at -= 1) {
    const parent = parents[at];
    switch (parent.type) {
      case "ConditionalExpression":
        if (parent.test === child) {
          return { kind: "test" };
        }
        child = parent;
        continue;
      case "LogicalExpression":
      case "ArrayExpression":
      case "SpreadElement":
      case "AwaitExpression":
        child = parent;
        continue;
      case "ReturnStatement":
        return { kind: "return" };
      case "ArrowFunctionExpression":
        return parent.body === child ? { kind: "return" } : { kind: "other" };
      case "CallExpression":
      case "NewExpression":
        return parent.callee === child ? { kind: "other" } : { kind: "call", name: calleeName(parent.callee), argIndex: parent.arguments.indexOf(child), call: parent };
      case "Property":
        return parent.value === child ? { kind: "prop", name: parent.key.type === "Identifier" ? parent.key.name : String(parent.key.value) } : { kind: "other" };
      case "AssignmentExpression": {
        if (parent.right !== child) {
          return { kind: "other" };
        }
        const left = parent.left;
        return { kind: "assign", name: left.type === "MemberExpression" && !left.computed ? left.property.name : left.type === "Identifier" ? left.name : "?" };
      }
      case "VariableDeclarator":
        return { kind: "var" };
      case "BinaryExpression":
        return ["==", "===", "!=", "!=="].includes(parent.operator) ? { kind: "compare" } : { kind: "other" };
      case "SwitchCase":
        return { kind: "compare" };
      case "ThrowStatement":
        return { kind: "throw" };
      case "ExpressionStatement":
        return { kind: "statement" };
      case "MemberExpression":
        return { kind: "member" };
      default:
        return { kind: "other" };
    }
  }
  return { kind: "other" };
}

/** Whether a literal with this text, given to `use`, is for the screen. */
function reachesTheScreen(text, use) {
  switch (use.kind) {
    case "compare":
    case "throw":
    case "test":
    case "statement":
      return false;
    case "member":
      return readsAsProse(text);
    case "call": {
      if (use.name === "el") {
        return use.argIndex > 1 ? isWords(text) : false;
      }
      if (/setAttribute$/.test(use.name) && use.argIndex === 1) {
        const first = use.call.arguments[0];
        return first.type === "Literal" && ARIA_ATTRIBUTES.has(first.value);
      }
      if (NOT_FOR_THE_SCREEN.test(use.name)) {
        return false;
      }
      if (SCREEN_CALLS.test(use.name) && use.argIndex >= 1 && isWords(text) && (use.name === "toast" || !/^[a-z]+$/.test(text))) {
        return true;
      }
      return readsAsProse(text);
    }
    case "prop":
      if (TEXT_PROPS.has(use.name)) {
        return isWords(text) && !/^[A-Za-z]+\/[A-Za-z]+$/.test(text);
      }
      return !NOT_TEXT_PROPS.has(use.name) && readsAsProse(text);
    case "assign":
      return TEXT_ASSIGNS.has(use.name) ? isWords(text) : !["className", "href", "id", "type", "value", "src", "name", "dataset", "rel", "target", "htmlFor", "role"].includes(use.name) && readsAsProse(text);
    default:
      return readsAsProse(text);
  }
}

/** The text of a literal node with each `${...}` place left empty (`${}`): its shape, which the scan reads, whatever the
 * expressions hold (a call with braces of its own would hide the words around it). */
function shapeOf(node) {
  if (node.type === "Literal") {
    return node.value;
  }
  return node.quasis.map((quasi, index) => quasi.value.cooked + (index < node.expressions.length ? "${}" : "")).join("");
}

/** The text of a literal node: a template's `${...}` places stay as they are written. */
function textOf(node, source) {
  if (node.type === "Literal") {
    return node.value;
  }
  return node.quasis.map((quasi, index) => quasi.value.cooked + (index < node.expressions.length ? `\${${source.slice(node.expressions[index].start, node.expressions[index].end)}}` : "")).join("");
}

/**
 * The words in `source` (the text of a module) that are written in the code and reach the screen: `{ line, text }` each.
 * @param {string} source
 * @returns {{ line: number, text: string }[]}
 */
export function hardcodedWords(source) {
  const ast = parse(source, { ecmaVersion: "latest", sourceType: "module", locations: true });
  const found = [];
  /** @param {any} node @param {any[]} parents */
  function visit(node, parents) {
    const parent = parents[parents.length - 1];
    const stringNode = node.type === "Literal" && typeof node.value === "string";
    const templateNode = node.type === "TemplateLiteral" && !(parent && parent.type === "TaggedTemplateExpression");
    const skipped =
      parent !== undefined &&
      (parent.type === "ImportDeclaration" ||
        parent.type === "ExportAllDeclaration" ||
        parent.type === "ExportNamedDeclaration" ||
        parent.type === "ExpressionStatement" ||
        parent.type === "PropertyDefinition" ||
        parent.type === "ImportExpression" ||
        (parent.type === "Property" && parent.key === node && !parent.computed) ||
        (parent.type === "MemberExpression" && parent.property === node));
    if ((stringNode || templateNode) && !skipped) {
      const shape = shapeOf(node);
      if (/[A-Za-z]{2,}/.test(bare(shape)) && reachesTheScreen(shape, consumer(node, parents))) {
        found.push({ line: node.loc.start.line, text: textOf(node, source).trim() });
      }
    }
    for (const key of Object.keys(node)) {
      if (key === "loc" || key === "start" || key === "end" || key === "type") {
        continue;
      }
      const value = node[key];
      if (Array.isArray(value)) {
        for (const child of value) {
          if (child !== null && typeof child.type === "string") {
            visit(child, [...parents, node]);
          }
        }
      } else if (value !== null && typeof value === "object" && typeof value.type === "string") {
        visit(value, [...parents, node]);
      }
    }
  }
  visit(ast, []);
  return found;
}

/** Every module of the interface under `directory`, as paths relative to the interface's root. */
export function modulesOf(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      return modulesOf(path);
    }
    return path.endsWith(".js") ? [relative(STATIC_ROOT, path)] : [];
  });
}

/** The source of the module `name` (a path relative to the interface's root). */
export function sourceOf(name) {
  return readFileSync(join(STATIC_ROOT, name), "utf8");
}

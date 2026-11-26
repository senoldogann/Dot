"""The script Dot runs in a page of the owner's Safari (item 1959), through Safari's ``do JavaScript``.

It is one function of one command, a JSON object, and returns a JSON string: Safari hands a script's last value back to
AppleScript as text, and does not wait for a promise, so every command does its work at once and a wait is Dot's own
poll of ``read`` from outside. The page is untrusted: the script only reads the document and acts on the element a ref
names; what it returns is parsed into a model before Dot uses it (:mod:`dotagent.safari.types`).

Refs (``s1``, ``s2``, …) live in the page's window, in a map kept under one property, so that a ref given at a read
names the same element at the next step and an element that stays on the page keeps its ref from one read to the
next. A navigation drops them with the page, as a new snapshot in Chrome does.
"""

import json

from dotagent.core.types import JsonObject

# The page script. ``cmd`` is the command Dot sends, written into the script as a JSON literal by :func:`page_command`.
_SCRIPT: str = r"""
(function (cmd) {
  "use strict";
  const KEY = "__dotSafariRefs";
  const state = window[KEY] || (window[KEY] = { n: 0, byRef: new Map(), refOf: new WeakMap() });
  const INTERACTIVE_ROLES = new Set([
    "button", "link", "checkbox", "radio", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "option",
    "switch", "combobox", "textbox", "searchbox", "spinbutton", "slider", "listbox", "treeitem"
  ]);
  const INPUT_ROLES = {
    checkbox: "checkbox", radio: "radio", button: "button", submit: "button", reset: "button", image: "button",
    file: "button", range: "slider", number: "spinbutton", search: "searchbox", color: "button"
  };
  const clean = (text, max) =>
    String(text || "").replace(/[\u0000-\u001f\u007f-\u009f\u00ad\u200b\u2028\u2029]/g, " ")
      .replace(/\s+/g, " ").trim().slice(0, max);

  function documents() {
    const found = [];
    const visit = (doc, depth) => {
      found.push(doc);
      if (depth >= 3) return;
      for (const frame of doc.querySelectorAll("iframe, frame")) {
        let inner = null;
        try { inner = frame.contentDocument; } catch (error) { inner = null; }
        if (inner && inner.documentElement) visit(inner, depth + 1);
      }
    };
    visit(document, 0);
    return found;
  }

  function elementsOf(root, into) {
    for (const element of root.querySelectorAll("*")) {
      into.push(element);
      if (element.shadowRoot) elementsOf(element.shadowRoot, into);
    }
    return into;
  }

  function roleOf(element) {
    const own = (element.getAttribute("role") || "").trim().split(/\s+/)[0];
    if (own) return clean(own, 40).toLowerCase();
    const tag = element.tagName.toLowerCase();
    if (tag === "a" && element.hasAttribute("href")) return "link";
    if (tag === "button" || tag === "summary") return "button";
    if (tag === "select") return element.multiple || element.size > 1 ? "listbox" : "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "input") return INPUT_ROLES[(element.type || "text").toLowerCase()] || "textbox";
    if (/^h[1-6]$/.test(tag)) return "heading";
    if (element.isContentEditable && !(element.parentElement && element.parentElement.isContentEditable)) {
      return "textbox";
    }
    return "generic";
  }

  function textOfIds(element, ids) {
    const doc = element.ownerDocument;
    return ids.split(/\s+/).map((id) => doc.getElementById(id)).filter(Boolean)
      .map((labelled) => labelled.innerText || labelled.textContent || "").join(" ");
  }

  function nameOf(element, role) {
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const named = clean(textOfIds(element, labelledBy), 160);
      if (named) return named;
    }
    const label = clean(element.getAttribute("aria-label"), 160);
    if (label) return label;
    const tag = element.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") {
      const type = (element.type || "").toLowerCase();
      if (["button", "submit", "reset"].includes(type) && element.value) return clean(element.value, 160);
      const labels = element.labels ? Array.from(element.labels).map((one) => one.innerText).join(" ") : "";
      const named = clean(labels, 160) || clean(element.getAttribute("placeholder"), 160) ||
        clean(element.getAttribute("title"), 160) || clean(element.getAttribute("alt"), 160);
      if (named) return named;
      return "";
    }
    if (tag === "img") return clean(element.getAttribute("alt") || element.getAttribute("title"), 160);
    if (role === "textbox") return clean(element.getAttribute("title") || element.getAttribute("placeholder"), 160);
    const shown = clean(element.innerText || element.textContent, 160);
    if (shown) return shown;
    const inside = element.querySelector && element.querySelector("img[alt], svg[aria-label]");
    if (inside) return clean(inside.getAttribute("alt") || inside.getAttribute("aria-label"), 160);
    return clean(element.getAttribute("title"), 160);
  }

  function visible(element) {
    const box = element.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) {
      // A checkbox or a radio button drawn by its label is often an input of no size: its label is what shows.
      const tag = element.tagName.toLowerCase();
      const type = (element.type || "").toLowerCase();
      if (!(tag === "input" && (type === "checkbox" || type === "radio") && element.labels && element.labels.length)) {
        return false;
      }
    }
    if (typeof element.checkVisibility === "function") {
      return element.checkVisibility({ checkVisibilityCSS: true });
    }
    const style = element.ownerDocument.defaultView.getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none";
  }

  function interactive(element, role) {
    if (INTERACTIVE_ROLES.has(role)) return true;
    const tag = element.tagName.toLowerCase();
    if (tag === "input") return (element.type || "").toLowerCase() !== "hidden";
    if (element.hasAttribute("onclick")) return true;
    const tabindex = element.getAttribute("tabindex");
    if (tabindex !== null && tabindex !== "-1" && role !== "generic") return true;
    return false;
  }

  function pointerOnly(element) {
    // A control a page drew from a div or a span: it shows a pointer that its parent does not.
    const tag = element.tagName.toLowerCase();
    if (!["div", "span", "li", "img", "label", "td", "p", "svg"].includes(tag)) return false;
    const view = element.ownerDocument.defaultView;
    if (view.getComputedStyle(element).cursor !== "pointer") return false;
    const parent = element.parentElement;
    return !parent || view.getComputedStyle(parent).cursor !== "pointer";
  }

  function refOf(element) {
    let ref = state.refOf.get(element);
    if (!ref || state.byRef.get(ref) !== element) {
      state.n += 1;
      ref = "s" + state.n;
      state.refOf.set(element, ref);
      state.byRef.set(ref, element);
    }
    return ref;
  }

  function fieldMark(element) {
    const tag = element.tagName.toLowerCase();
    if (!["input", "textarea", "select"].includes(tag)) return "";
    if ((element.type || "").toLowerCase() === "password") return "password";
    return String(element.getAttribute("autocomplete") || "");
  }

  function buttonsIn(root) {
    const found = [];
    for (const element of elementsOf(root, [])) {
      const role = roleOf(element);
      if ((role === "button" || role === "menuitem") && visible(element)) found.push(refOf(element));
    }
    return found;
  }

  function nearButtons(element) {
    // The buttons Enter in a text field may press: its form's, else those of its nearest small ancestors.
    const form = element.form || (element.closest && element.closest("form"));
    if (form) return buttonsIn(form);
    const found = [];
    let ancestor = element.parentElement;
    for (let level = 0; ancestor && level < 3; level += 1) {
      if (ancestor.getElementsByTagName("*").length > 80) break;
      for (const ref of buttonsIn(ancestor)) if (!found.includes(ref)) found.push(ref);
      ancestor = ancestor.parentElement;
    }
    return found;
  }

  function describe(element) {
    const role = roleOf(element);
    const tag = element.tagName.toLowerCase();
    const item = {
      ref: refOf(element), role: role, name: nameOf(element, role), tag: clean(tag, 40),
      mark: clean(fieldMark(element), 200)
    };
    if (tag === "input" || tag === "textarea") {
      const type = (element.type || "").toLowerCase();
      if (type === "checkbox" || type === "radio") item.checked = Boolean(element.checked);
      else if (!["button", "submit", "reset", "image", "file"].includes(type)) item.value = String(element.value || "");
    } else if (tag === "select") {
      const chosen = element.selectedOptions && element.selectedOptions[0];
      item.value = chosen ? clean(chosen.text, 160) : "";
      item.options = Array.from(element.options).slice(0, 30).map((option) => clean(option.text, 60));
    } else if (role === "textbox" && element.isContentEditable) {
      item.value = clean(element.innerText, 400);
    } else if (element.getAttribute("aria-checked") !== null) {
      item.checked = element.getAttribute("aria-checked") === "true";
    }
    if (["textbox", "searchbox", "combobox", "spinbutton", "checkbox", "radio"].includes(role)) {
      item.near = nearButtons(element);
    }
    if (element.disabled || element.getAttribute("aria-disabled") === "true") item.disabled = true;
    if (element.ownerDocument.activeElement === element) item.focused = true;
    if (role === "heading") {
      const level = tag.match(/^h([1-6])$/);
      item.level = Number(level ? level[1] : element.getAttribute("aria-level") || 0);
    }
    return item;
  }

  function read(limit, textChars) {
    const items = [];
    let more = 0;
    for (const doc of documents()) {
      for (const element of elementsOf(doc, [])) {
        const role = roleOf(element);
        const wanted = role === "heading" || interactive(element, role) || pointerOnly(element);
        if (!wanted || !visible(element)) continue;
        if (items.length >= limit) { more += 1; continue; }
        items.push(describe(element));
      }
    }
    const body = document.body ? document.body.innerText || "" : "";
    return {
      url: String(location.href), title: clean(document.title, 300), ready: document.readyState,
      elements: items, more: more, text: body.slice(0, textChars), text_chars: body.length
    };
  }

  function byRef(ref) {
    const element = state.byRef.get(ref);
    if (!element || !element.isConnected) return null;
    return element;
  }

  function pointer(element) {
    element.scrollIntoView({ block: "center", inline: "center" });
    const view = element.ownerDocument.defaultView;
    const init = { bubbles: true, cancelable: true, composed: true, view: view };
    for (const type of ["pointerover", "pointerdown", "mousedown", "pointerup", "mouseup"]) {
      const Kind = type.startsWith("pointer") && view.PointerEvent ? view.PointerEvent : view.MouseEvent;
      element.dispatchEvent(new Kind(type, init));
    }
    if (typeof element.focus === "function") element.focus({ preventScroll: true });
    element.click();
  }

  function setValue(element, text) {
    const view = element.ownerDocument.defaultView;
    element.scrollIntoView({ block: "center", inline: "center" });
    element.focus({ preventScroll: true });
    if (element.isContentEditable && !("value" in element)) {
      const range = element.ownerDocument.createRange();
      range.selectNodeContents(element);
      const selection = view.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      element.ownerDocument.execCommand("insertText", false, text);
      return clean(element.innerText, 100000) === clean(text, 100000);
    }
    const proto = element.tagName.toLowerCase() === "textarea" ? view.HTMLTextAreaElement.prototype
      : view.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(element, text);
    const typed = { bubbles: true, composed: true, inputType: "insertText", data: text };
    element.dispatchEvent(new view.InputEvent("input", typed));
    element.dispatchEvent(new view.Event("change", { bubbles: true }));
    return element.value === text;
  }

  function choose(element, text) {
    const view = element.ownerDocument.defaultView;
    const wanted = clean(text, 200).toLowerCase();
    const options = Array.from(element.options || []);
    const option = options.find((one) => clean(one.text, 200).toLowerCase() === wanted) ||
      options.find((one) => String(one.value).toLowerCase() === wanted);
    if (!option) return { done: false, detail: "no option is named " + clean(text, 60) };
    element.value = option.value;
    element.dispatchEvent(new view.Event("input", { bubbles: true }));
    element.dispatchEvent(new view.Event("change", { bubbles: true }));
    return { done: true, verified: element.value === option.value, detail: "chose " + clean(option.text, 60) };
  }

  function checked(element) {
    if ("checked" in element && (element.type === "checkbox" || element.type === "radio")) return element.checked;
    return element.getAttribute("aria-checked") === "true";
  }

  function press(element, key) {
    const target = element || document.activeElement || document.body;
    const view = target.ownerDocument.defaultView;
    const name = String(key);
    const init = { key: name, code: name, bubbles: true, cancelable: true, composed: true };
    const kept = target.dispatchEvent(new view.KeyboardEvent("keydown", init));
    target.dispatchEvent(new view.KeyboardEvent("keyup", init));
    if (!kept) return "the page handled " + name;
    if (name === "Enter") {
      const tag = target.tagName.toLowerCase();
      if (tag === "button" || tag === "a" || roleOf(target) === "button") { target.click(); return "pressed it"; }
      const form = target.form || (target.closest && target.closest("form"));
      if (form) {
        if (typeof form.requestSubmit === "function") form.requestSubmit(); else form.submit();
        return "sent its form";
      }
    }
    return "sent " + name;
  }

  function step(command) {
    const element = command.ref ? byRef(command.ref) : null;
    if (command.ref && !element) {
      return { done: false, detail: "no element " + command.ref + " on the page now: read the page again" };
    }
    if (element && typeof command.name === "string" && nameOf(element, roleOf(element)) !== command.name) {
      return { done: false, detail: "element " + command.ref + " no longer shows what was read: read the page again" };
    }
    switch (command.action) {
      case "click":
        pointer(element);
        return { done: true, detail: "clicked" };
      case "fill":
        if (element.tagName.toLowerCase() === "select") return choose(element, command.text);
        return { done: true, verified: setValue(element, command.text), detail: "filled" };
      case "select":
        return choose(element, command.text);
      case "check":
      case "uncheck": {
        const wanted = command.action === "check";
        if (checked(element) !== wanted) pointer(element);
        return { done: true, verified: checked(element) === wanted, detail: wanted ? "checked" : "unchecked" };
      }
      case "press":
        if (element) element.focus({ preventScroll: true });
        return { done: true, detail: press(element, command.key) };
      case "scroll_into_view":
        element.scrollIntoView({ block: "center", inline: "center" });
        return { done: true, detail: "scrolled into view" };
      default:
        return { done: false, detail: "unknown action" };
    }
  }

  try {
    if (cmd.op === "read") return JSON.stringify({ ok: true, page: read(cmd.limit, cmd.text_chars) });
    if (cmd.op === "step") return JSON.stringify({ ok: true, step: step(cmd.step), url: String(location.href) });
    if (cmd.op === "where") {
      const body = document.body ? document.body.innerText || "" : "";
      const where = { ok: true, url: String(location.href), ready: document.readyState };
      return JSON.stringify(Object.assign(where, { text: body.slice(0, cmd.text_chars) }));
    }
    return JSON.stringify({ ok: false, error: "unknown op" });
  } catch (error) {
    return JSON.stringify({ ok: false, error: clean(String(error && error.message || error), 300) });
  }
})
"""


def page_command(command: JsonObject) -> str:
    """The page script called with ``command``, as the source ``do JavaScript`` runs. The command is a JSON literal in
    ASCII, so no character of it (a quote, U+2028) can end the literal or break the script."""
    return f"{_SCRIPT.strip()}({json.dumps(command, ensure_ascii=True)})"

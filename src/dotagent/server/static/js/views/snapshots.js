// Settings › SnapShots (item 405): a double tap of the Command key, in any application, captures the window in front
// into the open chat's box; what comes with it, the sound, the spring, and whether the key is watched right now.

import { api } from "../api.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import { choiceSetting, errorPanel, loadingPanel, paneHead, switchSetting } from "../panels.js";
import { attempt, toast } from "../ui.js";

const SOUNDS = [
  { id: "click", label: "Click", hint: "A short click" },
  { id: "pop", label: "Pop", hint: "A soft pop" },
  { id: "glass", label: "Glass", hint: "A glass note" },
  { id: "off", label: "Off", hint: "No sound" },
];

export function mount(root, _ctx) {
  let disposed = false;
  let view = null;
  const holder = el("div", { class: "stack" }, loadingPanel());

  async function change(key, value) {
    view = await api.post("/snapshots/settings", { key, value });
  }

  function listening() {
    if (!view.enabled) {
      return el("p", { class: "hint" }, "Off: the Command key is not watched.");
    }
    if (view.problem !== null) {
      // The pane a press away (item 409); the watch starts by itself once the permission is granted.
      return el(
        "div",
        { class: "stack tight" },
        el("p", { class: "hint snapshots-problem" }, icon("alert"), el("span", null, ` ${view.problem}`)),
        el(
          "div",
          { class: "cluster" },
          el(
            "button",
            { class: "btn sm", type: "button", onclick: () => attempt(() => api.post("/desktop/permissions/open", { pane: "accessibility" })) },
            icon("external"),
            "Open System Settings",
          ),
        ),
      );
    }
    return el("p", { class: "hint" }, view.listening ? `Listening for ${view.shortcut}.` : "Starting the watch of the key…");
  }

  function paint() {
    const enabled = switchSetting({
      label: "SnapShots",
      hint: "Capture a window and attach it to your current draft.",
      read: () => view.enabled,
      write: (on) => change("enabled", on),
      onToggled: () => {
        if (!disposed) {
          paint();
          toast("ok", view.enabled ? "SnapShots are on." : "SnapShots are off.");
        }
      },
    });
    enabled.paint();
    const appText = switchSetting({
      label: "Include app text",
      hint: "Include text and controls when the app makes them available: a Markdown file beside the picture.",
      read: () => view.app_text,
      write: (on) => change("app_text", on),
      onToggled: () => {
        if (!disposed) {
          appText.paint();
        }
      },
    });
    appText.paint();
    const shortcut = el(
      "div",
      { class: "setting" },
      el("div", { class: "row" }, el("span", { class: "grow" }, "Capture shortcut"), el("kbd", { class: "snapshots-keys" }, view.shortcut)),
      el(
        "div",
        { class: "hint" },
        "Capture the window you're using without switching apps: press both Command keys together, or tap the Command key twice. Another key in between, as in ⌘C ⌘V, is not a capture. Dot's own window and a password manager's are never captured.",
      ),
    );
    const sound = choiceSetting({
      label: "Capture sound",
      hint: "Choose the sound played when capture starts.",
      options: SOUNDS,
      selected: view.sound,
      onSelect: (id) => attempt(() => change("sound", id)),
    });
    const animations = switchSetting({
      label: "Capture animations",
      hint: "Animate captured windows into your draft.",
      read: () => view.animations,
      write: (on) => change("animations", on),
      onToggled: () => {
        if (!disposed) {
          animations.paint();
        }
      },
    });
    animations.paint();
    const flash = switchSetting({
      label: "Capture flash",
      hint: "A soft flash over the window as it is captured.",
      read: () => view.flash,
      write: (on) => change("flash", on),
      onToggled: () => {
        if (!disposed) {
          flash.paint();
        }
      },
    });
    flash.paint();
    const openDot = switchSetting({
      label: "Open Dot after a capture",
      hint: "Bring Dot's window, or its tab in your browser, to the front with the capture in the box. macOS asks once whether Dot may choose the browser's tab.",
      read: () => view.open_dot,
      write: (on) => change("open_dot", on),
      onToggled: () => {
        if (!disposed) {
          openDot.paint();
        }
      },
    });
    openDot.paint();
    holder.replaceChildren(
      el(
        "section",
        { class: "card", "aria-label": "SnapShots" },
        el("div", { class: "card-head" }, el("span", { class: "grow" }, "SnapShots")),
        el("div", { class: "card-pad stack" }, enabled.element, listening(), appText.element, shortcut, sound.element, animations.element, flash.element, openDot.element),
      ),
    );
  }

  async function load() {
    try {
      view = await api.get("/snapshots");
    } catch (error) {
      if (!disposed) {
        holder.replaceChildren(errorPanel(error, () => attempt(load)));
      }
      return;
    }
    if (!disposed) {
      paint();
    }
  }

  root.append(paneHead("A double tap of ⌘ in any application captures the window in front into the open chat's box.", null), holder);
  attempt(load);
  return () => {
    disposed = true;
  };
}

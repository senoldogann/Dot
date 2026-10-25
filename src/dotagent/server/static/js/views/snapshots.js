// Settings › SnapShots (item 405): a double tap of the Command key, in any application, captures the window in front
// into the open chat's box; what comes with it, the sound, the spring, and whether the key is watched right now.

import { api } from "../api.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import { choiceRowSetting, errorPanel, loadingPanel, setCard, setRow, setSection, switchRowSetting } from "../panels.js";
import { attempt, toast } from "../ui.js";

const SOUNDS = [
  { id: "click", label: "Click", hint: "A short click" },
  { id: "pop", label: "Pop", hint: "A soft pop" },
  { id: "glass", label: "Glass", hint: "A glass note" },
  { id: "off", label: "Off", hint: "No sound" },
];

export function mount(root, ctx) {
  let disposed = false;
  let view = null;
  const holder = el("div", { class: "stack" }, loadingPanel());

  async function change(key, value) {
    view = await api.post("/snapshots/settings", { key, value });
  }

  /** The row that says whether the Command key is watched; when macOS has not allowed it, the pane is a press away (item 409) and the
   * watch starts by itself once the permission is granted. */
  function listening() {
    if (!view.enabled) {
      return setRow({ title: "The Command key", lead: "Off: the Command key is not watched." });
    }
    if (view.problem !== null) {
      return setRow({
        title: "The Command key",
        lead: el("span", { class: "snapshots-problem" }, icon("alert"), el("span", null, ` ${view.problem}`)),
        control: el(
          "button",
          { class: "btn sm", type: "button", onclick: () => attempt(() => api.post("/desktop/permissions/open", { pane: "accessibility" })) },
          icon("external"),
          "Open System Settings",
        ),
      });
    }
    return setRow({ title: "The Command key", lead: view.listening ? `Listening for ${view.shortcut}.` : "Starting the watch of the key…" });
  }

  function paint() {
    const enabled = switchRowSetting({
      label: "SnapShots",
      hint: "Capture a window and attach it to your current draft.",
      read: () => view.enabled,
      // The words follow the change once it is made (item 559): a switch that failed says so alone.
      write: async (on) => {
        await change("enabled", on);
        toast("ok", view.enabled ? "SnapShots are on." : "SnapShots are off.");
      },
      onToggled: () => {
        if (!disposed) {
          paint();
        }
      },
    });
    enabled.paint();
    const appText = switchRowSetting({
      label: "Include app text",
      hint: "Include the text and buttons of the window when the app makes them available: a text file beside the picture.",
      read: () => view.app_text,
      write: (on) => change("app_text", on),
      onToggled: () => {
        if (!disposed) {
          appText.paint();
        }
      },
    });
    appText.paint();
    const shortcut = setRow({
      title: "Capture shortcut",
      lead: "Capture the window you're using without switching apps: press both Command keys together, or tap the Command key twice. Another key in between, as in ⌘C ⌘V, is not a capture. Dot's own window and a password manager's are never captured.",
      control: el("kbd", { class: "snapshots-keys" }, view.shortcut),
    });
    const sound = choiceRowSetting({
      label: "Capture sound",
      hint: "Choose the sound played when capture starts.",
      options: SOUNDS,
      selected: view.sound,
      onSelect: (id) => attempt(() => change("sound", id)),
    });
    const animations = switchRowSetting({
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
    const flash = switchRowSetting({
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
    const openDot = switchRowSetting({
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
        "div",
        { class: "set-sections", role: "region", "aria-label": "SnapShots" },
        setSection({ title: "Capture", body: setCard(enabled.element, listening(), appText.element, shortcut) }),
        setSection({ title: "When it captures", body: setCard(sound.element, animations.element, flash.element, openDot.element) }),
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

  ctx.head.setLead("A double tap of ⌘ in any app captures the window in front into the box of the open chat.");
  root.append(holder);
  attempt(load);
  return () => {
    disposed = true;
  };
}

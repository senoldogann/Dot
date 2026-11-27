// Settings › Snapshots (item 405): both Command keys pressed together (and, when it is switched on, a double tap of one,
// item 814), in any application, capture the window in front into the open chat's box; what comes with it, the sound,
// the spring, and whether the keys are watched right now.

import { api } from "../api.js";
import { el } from "../dom.js";
import { icon } from "../icons.js";
import { choiceRowSetting, errorPanel, loadingPanel, setCard, setRow, setSection, switchRowSetting } from "../panels.js";
import { attempt, toast } from "../ui.js";
import { t } from "../i18n.js";

const SOUNDS = [
  { id: "click", label: t("views.snapshots.click"), hint: t("views.snapshots.aShortClick") },
  { id: "pop", label: t("views.snapshots.pop"), hint: t("views.snapshots.aSoftPop") },
  { id: "glass", label: t("views.snapshots.glass"), hint: t("views.snapshots.aGlassNote") },
  { id: "off", label: t("views.snapshots.off"), hint: t("views.snapshots.noSound") },
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
      return setRow({ title: t("views.snapshots.theCommandKey"), lead: t("views.snapshots.offTheCommandKeyIs") });
    }
    if (view.problem !== null) {
      return setRow({
        title: t("views.snapshots.theCommandKey"),
        lead: el("span", { class: "snapshots-problem" }, icon("alert"), el("span", null, ` ${view.problem}`)),
        control: el(
          "button",
          { class: "btn sm", type: "button", onclick: () => attempt(() => api.post("/desktop/permissions/open", { pane: "accessibility" })) },
          icon("external"),
          t("views.snapshots.openSystemSettings"),
        ),
      });
    }
    return setRow({ title: t("views.snapshots.theCommandKey"), lead: view.listening ? t("views.snapshots.listeningForShortcut", { shortcut: view.shortcut }) : t("views.snapshots.startingTheWatchOfThe") });
  }

  function paint() {
    const enabled = switchRowSetting({
      label: t("views.snapshots.snapshots"),
      hint: t("views.snapshots.captureAWindowAndAttach"),
      read: () => view.enabled,
      // The words follow the change once it is made (item 559): a switch that failed says so alone.
      write: async (on) => {
        await change("enabled", on);
        toast("ok", view.enabled ? t("views.snapshots.snapshotsAreOn") : t("views.snapshots.snapshotsAreOff"));
      },
      onToggled: () => {
        if (!disposed) {
          paint();
        }
      },
    });
    enabled.paint();
    const appText = switchRowSetting({
      label: t("views.snapshots.includeAppText"),
      hint: t("views.snapshots.includeTheTextAndButtons"),
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
      title: t("views.snapshots.captureShortcut"),
      lead: t("views.snapshots.captureTheWindowYouRe"),
      control: el("kbd", { class: "snapshots-keys" }, view.shortcut),
    });
    const doubleTap = switchRowSetting({
      label: t("views.snapshots.doubleTapOfToo"),
      hint: t("views.snapshots.alsoCaptureWhenOneCommand"),
      read: () => view.double_tap,
      write: (on) => change("double_tap", on),
      onToggled: () => {
        if (!disposed) {
          doubleTap.paint();
        }
      },
    });
    doubleTap.paint();
    const sound = choiceRowSetting({
      label: t("views.snapshots.captureSound"),
      hint: t("views.snapshots.chooseTheSoundPlayedWhen"),
      options: SOUNDS,
      selected: view.sound,
      onSelect: (id) => attempt(() => change("sound", id)),
    });
    const animations = switchRowSetting({
      label: t("views.snapshots.captureAnimations"),
      hint: t("views.snapshots.animateCapturedWindowsIntoYour"),
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
      label: t("views.snapshots.captureFlash"),
      hint: t("views.snapshots.aSoftFlashOverThe"),
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
      label: t("views.snapshots.openDotAfterACapture"),
      hint: t("views.snapshots.bringDotSWindowOr"),
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
        { class: "set-sections", role: "region", "aria-label": t("views.snapshots.snapshots") },
        setSection({ title: t("views.snapshots.capture"), body: setCard(enabled.element, listening(), appText.element, shortcut, doubleTap.element) }),
        setSection({ title: t("views.snapshots.whenItCaptures"), body: setCard(sound.element, animations.element, flash.element, openDot.element) }),
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

  ctx.head.setLead(t("views.snapshots.bothCommandKeysPressedTogether"));
  root.append(holder);
  attempt(load);
  return () => {
    disposed = true;
  };
}

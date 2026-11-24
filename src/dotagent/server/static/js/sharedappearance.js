import { request } from "./api.js";

/** @typedef {{canvas: string, surface: string, raised: string, text: string, muted: string, accent: string}} SharedPalette */
/** @typedef {{name: string, light: SharedPalette, dark: SharedPalette, artwork: "quiet-forest" | "custom" | null}} ActiveTheme */
/** @typedef {{revision: number, active: ActiveTheme | null, previous: ActiveTheme | null}} AppearanceSnapshot */

export async function readSharedAppearance(signal) {
  /** @type {AppearanceSnapshot} */
  const appearance = await request("GET", "/active-theme", undefined, signal);
  return appearance;
}

/** @param {number} revision @param {"forest" | "undo" | "local"} choice */
export async function applySharedAppearance(revision, choice) {
  await request("PUT", "/active-theme", { revision, choice });
}

const ROLES = ["canvas", "surface", "raised", "text", "muted", "accent"];

/** @param {ActiveTheme | null} theme @param {string} image */
function paintSharedAppearance(theme, image) {
  const root = document.documentElement;
  root.toggleAttribute("data-shared-appearance", theme !== null);
  for (const role of ROLES) {
    if (theme !== null) {
      root.style.setProperty(`--shared-${role}`, `light-dark(${theme.light[role]}, ${theme.dark[role]})`);
    } else {
      root.style.removeProperty(`--shared-${role}`);
    }
  }
  root.style.setProperty("--shared-artwork", image);
}

/** Follow the shared appearance while this signed-in window is visible. @param {AbortSignal} signal */
export function followSharedAppearance(signal) {
  let revision = -1;
  let pending = false;
  async function refresh() {
    if (document.hidden || signal.aborted || pending) {
      return;
    }
    pending = true;
    try {
      const appearance = await readSharedAppearance(signal);
      if (appearance.revision === revision) {
        return;
      }
      let image = "none";
      if (appearance.active?.artwork === "quiet-forest") {
        image = 'url("/images/quiet-forest.jpg")';
      } else if (appearance.active?.artwork === "custom") {
        const { jpeg } = await request("GET", `/active-theme/artwork?revision=${appearance.revision}`, undefined, signal);
        if (typeof jpeg !== "string" || !/^[a-zA-Z0-9+/]+={0,2}$/.test(jpeg)) {
          throw new Error("Dot returned invalid appearance artwork.");
        }
        image = `url("data:image/jpeg;base64,${jpeg}")`;
      }
      if (!signal.aborted) {
        paintSharedAppearance(appearance.active, image);
        revision = appearance.revision;
      }
    } catch (error) {
      if (!signal.aborted) {
        console.debug("Could not read the shared appearance", error);
      }
    } finally {
      pending = false;
    }
  }
  const timer = window.setInterval(refresh, 2500);
  document.addEventListener("visibilitychange", refresh, { signal });
  window.addEventListener("online", refresh, { signal });
  signal.addEventListener("abort", () => {
    window.clearInterval(timer);
    paintSharedAppearance(null, "none");
  }, { once: true });
  void refresh();
}

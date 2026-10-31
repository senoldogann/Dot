// three.js for the office (the Office view of the Agents page): the library is vendored under /vendor/three (0.187.0, its
// MIT licence beside it) and loaded only when the office opens, so no other page pays for it. Its types come from the
// development dependency @types/three. `THREE` is the loaded module, a live binding: read it only after `loadThree()`.

// A variable, so that the type check does not follow the vendored file; the module's type is the package's.
const VENDORED = "/vendor/three/three.module.js";

/** @type {ThreeModule} */
export let THREE;

/** Load three.js once; the same module after that. */
export async function loadThree() {
  if (THREE === undefined) {
    THREE = /** @type {ThreeModule} */ (await import(VENDORED));
  }
  return THREE;
}

/** Whether this browser draws WebGL 2: the office needs it, and the Agents page shows the list without it. */
export function canDraw() {
  try {
    const canvas = document.createElement("canvas");
    const probe = canvas.getContext("webgl2");
    // The probe's context is given back at once: a browser keeps only so many.
    probe?.getExtension("WEBGL_lose_context")?.loseContext();
    return probe !== null;
  } catch {
    return false;
  }
}

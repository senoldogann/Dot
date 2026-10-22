// The palette of the composer's menus (`GET /palette`: the skills, the MCP servers with their tools and the accounts),
// asked for once per page and kept, and the marks of the MCP servers by name, which every chip that names a server wears
// as the at menu and Customize do (item 530): composer.js reads the palette for its menus, picks.js draws the chips.

import { api } from "./api.js";

let paletteLoad = null;
let wanted = false;
// The mark of each server the palette lists, by its name: the service's (`{ logo, glyph, monogram, colour }`), or null
// for a server of no service.
const serverMarks = new Map();
const listeners = [];

/** The palette, asked for once and kept; a failure is not kept, so that the next ask tries again. The marks of its
 * servers are remembered, and `onServerMarks` listeners told, as it arrives. */
export function loadPalette() {
  if (paletteLoad === null) {
    paletteLoad = api.get("/palette").then(
      (palette) => {
        for (const server of palette.servers) {
          serverMarks.set(server.name, server.mark ?? null);
        }
        // A chip that cannot be drawn again must not keep the palette from the menus: each listener on its own.
        for (const listener of listeners) {
          try {
            listener();
          } catch (error) {
            console.warn("a chip could not be drawn again with the marks of the MCP servers", error);
          }
        }
        return palette;
      },
      (error) => {
        paletteLoad = null;
        throw error;
      },
    );
  }
  return paletteLoad;
}

/** The mark of the MCP server `name` as the palette lists it, or null for a server of no service and for one the
 * palette has not named (yet), which wear the plug. */
export function serverMark(name) {
  return serverMarks.get(name) ?? null;
}

/** Ask for the palette, once, for the marks of the servers a chip names: a draft kept, or a message sent, names its
 * servers before the menu that lists them has been opened. A failure is told in the console and the chips keep the plug;
 * the menu says it to the owner when it is opened. */
export function wantServerMarks() {
  if (wanted) {
    return;
  }
  wanted = true;
  loadPalette().catch((error) => console.warn("the marks of the MCP servers could not be read", error));
}

/** Have `listener()` run each time the palette arrives and its marks are remembered. */
export function onServerMarks(listener) {
  listeners.push(listener);
}

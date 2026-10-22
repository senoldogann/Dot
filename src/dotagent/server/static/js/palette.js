// The palette of the composer's menus (`GET /palette`: the skills, the MCP servers with their tools and the accounts),
// asked for once per page and kept, and the marks of the MCP servers by name, which every chip that names a server wears
// as the at menu and Customize do (item 530): composer.js reads the palette for its menus, picks.js draws the chips.

import { api } from "./api.js";

/** A palette read by `read()` (a promise of `GET /palette`'s answer): `loadPalette()` asks once and keeps the answer, a
 * failure is not kept, so that the next ask tries again; `serverMark(name)` is the mark of an MCP server the palette
 * lists (`{ logo, glyph, monogram, colour }`, or null for a server of no service and for one it has not named yet, which
 * wear the plug); `wantServerMarks()` asks for the palette, once, for the chips that name a server before any menu that
 * lists them has been opened (a draft kept, a message sent); `onServerMarks(listener)` runs `listener()` each time the
 * palette arrives and its marks are remembered. */
export function createPalette(read) {
  let paletteLoad = null;
  let wanted = false;
  const serverMarks = new Map();
  const listeners = [];

  function loadPalette() {
    if (paletteLoad === null) {
      paletteLoad = read().then(
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

  return {
    loadPalette,
    serverMark: (name) => serverMarks.get(name) ?? null,
    // A failure is told in the console and the chips keep the plug; the menu says it to the owner when it is opened.
    wantServerMarks: () => {
      if (wanted) {
        return;
      }
      wanted = true;
      loadPalette().catch((error) => console.warn("the marks of the MCP servers could not be read", error));
    },
    onServerMarks: (listener) => {
      listeners.push(listener);
    },
  };
}

const shared = createPalette(() => api.get("/palette"));

/** The palette of the page, asked for once and kept (see `createPalette`). */
export const loadPalette = shared.loadPalette;
/** The mark of the MCP server `name` as the palette lists it, or null for a server of no service and for one the
 * palette has not named (yet), which wear the plug. */
export const serverMark = shared.serverMark;
/** Ask for the palette, once, for the marks of the servers a chip names. */
export const wantServerMarks = shared.wantServerMarks;
/** Have `listener()` run each time the palette arrives and its marks are remembered. */
export const onServerMarks = shared.onServerMarks;

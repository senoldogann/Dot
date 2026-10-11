// The top bar's middle and its page tools (item 78). The bar belongs to the shell; a page names itself in the bar's
// middle (its title, or on the run page Dot's face with the run's state and start) and may put its own tools before
// the daemon's pills. The router clears both before a page mounts, so a page that sets nothing leaves the bar bare.
import { el } from "./dom.js";

const title = el("div", { class: "stage-title" });
const tools = el("div", { class: "stage-tools" });

/** The bar's middle, for the shell to place once. */
export function stageTitleSlot() {
  return title;
}

/** The bar's page tools, for the shell to place once, before its own pills. */
export function stageToolsSlot() {
  return tools;
}

/** The nodes that are there: a page passes null for a chip it has no reason to show. */
function present(nodes) {
  return nodes.filter((node) => node !== null && node !== undefined);
}

/** Name the page in the bar: the nodes replace what was there. */
export function setStageTitle(...nodes) {
  title.replaceChildren(...present(nodes));
}

/** Put a page's tools in the bar: the nodes replace what was there. */
export function setStageTools(...nodes) {
  tools.replaceChildren(...present(nodes));
}

/** Empty the bar's middle and tools (the router, before a page mounts). */
export function clearStage() {
  title.replaceChildren();
  tools.replaceChildren();
}

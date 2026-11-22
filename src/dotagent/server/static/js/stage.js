// The top bar's middle and its page tools (item 78). The bar belongs to the shell; a page names itself in the bar's
// middle (its title, or on a chat's page its agent and the chat's title as a breadcrumb, item 1329) and may put its
// own tools before the daemon's pills. The router clears both before a page mounts, so a page that sets nothing leaves the bar bare.
import { badge } from "./badge.js";
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

/** Whether a node is the face of an agent or of a team, which a page puts before its name. */
function isFace(node) {
  return node instanceof Element && (node.classList.contains("avatar") || node.classList.contains("team-duo"));
}

// What copies the state of the face a page keeps updating (working, waiting, done, failed) to the badge that stands for it.
let faceWatch = null;

/** The badge the bar shows for the agent `name` whose face a page keeps: the face stays inside it, out of sight, and its state
 * (`data-state`) is the badge's, so that a run that waits or failed is told by the badge's tint (shell.css) and no mascot
 * stands in the bar. */
function badgeOf(face, name) {
  const mark = badge(name);
  mark.classList.add("stage-badge");
  mark.append(face);
  const copy = () => {
    mark.dataset.state = face.dataset.state ?? "";
  };
  copy();
  faceWatch?.disconnect();
  faceWatch = new MutationObserver(copy);
  faceWatch.observe(face, { attributes: true, attributeFilter: ["data-state"] });
  return mark;
}

/** Name the page in the bar: the nodes replace what was there. The face a page puts before an agent's name is that agent's
 * badge in the bar (item 1329): the bar is as plain as a breadcrumb, whoever's page it is. */
export function setStageTitle(...nodes) {
  const shown = present(nodes);
  const named = shown.find((node) => node instanceof Element && node.classList.contains("stage-name"));
  faceWatch?.disconnect();
  faceWatch = null;
  title.replaceChildren(...shown.map((node) => (isFace(node) && named !== undefined ? badgeOf(node, named.textContent ?? "") : node)));
}

/** Put a page's tools in the bar: the nodes replace what was there. */
export function setStageTools(...nodes) {
  tools.replaceChildren(...present(nodes));
}

/** Mark the bar of a chat, an agent's page and a new chat (items 1119 and 1123), whose bars were panes of glass over the page
 * on a phone; since item 1329 every page's bar is the same plain one, and the mark only says which pages these are. The
 * router takes it away with the rest. */
export function setStageGlass() {
  title.closest(".stagebar")?.setAttribute("data-glass", "");
}

/** Empty the bar's middle and tools (the router, before a page mounts). */
export function clearStage() {
  faceWatch?.disconnect();
  faceWatch = null;
  title.replaceChildren();
  tools.replaceChildren();
  title.closest(".stagebar")?.removeAttribute("data-glass");
}

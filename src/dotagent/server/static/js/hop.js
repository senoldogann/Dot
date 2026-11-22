// A chat chosen in the sidebar on a phone takes the place of the one before it in the history (item 1321). The sidebar's
// pages start anew (they are no page's link, `main.js`), so nothing leads back to the chat left, but a push of the
// history put it behind the new one all the same: the edge swipe of iOS (a Home Screen app, Safari) goes back through
// the history on its own, before any script sees the touch, and showed the chat before instead of the list of chats
// the finger meant to draw out. With one place in the history there is nothing to go back to, and the swipe is Dot's
// own (`shell.js`, which opens the sidebar).

const TOUCH = "(hover: none) and (pointer: coarse)";

/** Whether a click on a link takes the place of the page before it in the history, not a new place of its own.
 * `plain` is a click of the main button alone; `inSidebar` that the link is in the sidebar; `sameTab` that it opens in
 * this tab; `phone` that the screen is a phone's width and `touch` that it is a finger's, not a pointer's. */
export function takesThePlace({ plain, inSidebar, sameTab, phone, touch }) {
  return plain && inSidebar && sameTab && phone && touch;
}

/** Go to the page `route` (`#/runs/...`): on a phone's touch screen in the place of the page left, elsewhere as a new
 * place of the history. The ways a chat opens besides a link of the sidebar (a notification tapped, the toast's Open, the
 * home after the chat open was deleted) go through it, for the same swipe. */
export function goTo(route) {
  // The width of layout.js's PHONE, which this module does not load: it is read where it is needed, and by tests.
  if (window.matchMedia("(max-width: 720px)").matches && touchScreen()) {
    location.replace(route);
  } else {
    location.hash = route;
  }
}

/** Whether this screen is a finger's. */
export function touchScreen() {
  return window.matchMedia(TOUCH).matches;
}

"""A finger's hold of a message (item 1064), which opens its sheet: pointer events of the touch kind, which
Playwright's touch screen has no way to hold. Shared by the browser tests of a phone's conversation."""

# A press that rests this long on a message is a hold (messagesheet.js holds at 450 ms).
HOLD_MS: int = 700
# A touch that rests on `selector`, as a finger's does: pointer events of the touch kind, which Playwright's touch
# screen has no way to hold.
HOLD: str = """async ([selector, ms]) => {
  const target = document.querySelector(selector);
  const box = target.getBoundingClientRect();
  const at = { clientX: box.left + box.width / 2, clientY: box.top + Math.min(12, box.height / 2), bubbles: true,
    pointerType: "touch", pointerId: 7, isPrimary: true };
  target.dispatchEvent(new PointerEvent("pointerdown", at));
  await new Promise((done) => setTimeout(done, ms));
  target.dispatchEvent(new PointerEvent("pointerup", at));
}"""

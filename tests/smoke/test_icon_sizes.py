"""Every icon draws about the same area at the same size (item 849): the owner saw the speaker of an answer's foot, and
the "+" and the microphone of the box, smaller than the copy beside them, all in boxes of one size, since the shapes
fill their 24 grid unequally. Each icon is drawn in Chrome as the page draws it, at 240 px, and what it draws is
measured: its area is that of a square of 18.5 within a tenth, its longer side at most 22 and its middle at the box's,
and its stroke as thick on the screen as an icon's that fills its grid. The chevrons, a fold's mark, keep their size.
The page's own files, served to the browser here; nothing leaves the Mac."""

from pathlib import Path

import pytest
from playwright.async_api import Route, async_playwright

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

_STATIC: Path = Path(__file__).resolve().parents[2] / "src" / "dotagent" / "server" / "static"
_ORIGIN: str = "http://icons.test"
# The marks of a fold, which keep their own small size.
_OWN_SIZE: frozenset[str] = frozenset({"chevron", "chevronLeft"})
# What every icon draws, in the 24 units of a box: the area of an 18.5 by 18.5 square within a tenth (a line of three
# dots draws no area, so its width is read), its longer side at most 22 and a half, its middle within one of the box's.
_AREA: float = 18.5 * 18.5
_MOST_SIDE: float = 22.5
_MEASURE: str = """async () => {
  const { icon } = await import('/js/icons.js');
  const source = await (await fetch('/js/icons.js')).text();
  const names = [...new Set([...source.matchAll(/^  (\\w+): \\[/gm)].map((found) => found[1]))];
  const shapes = names.filter((name) => { try { icon(name); return true; } catch { return false; } });
  const out = [];
  for (const name of shapes) {
    const node = icon(name);
    node.setAttribute('width', '240');
    node.setAttribute('height', '240');
    document.body.append(node);
    const box = node.getBBox();
    // The frame as the browser took it: a viewBox it could not read is none, and fails here.
    const view = node.viewBox.baseVal;
    const unit = 24 / view.width;
    const stroke = parseFloat(getComputedStyle(node).strokeWidth) * (240 / view.width);
    // A close frame leaves half the stroke past its edge, which the icon draws rather than cuts.
    const cut = getComputedStyle(node).overflow !== 'visible';
    if (view.width === 0 || view.width !== view.height) {
      out.push({ name, w: 0, h: 0, cx: 0, cy: 0, stroke: 0, unread: node.getAttribute('viewBox') });
      node.remove();
      continue;
    }
    out.push({ name, w: box.width * unit, h: box.height * unit,
      cx: (box.x - view.x + box.width / 2) * unit, cy: (box.y - view.y + box.height / 2) * unit, stroke, cut });
    node.remove();
  }
  return out;
}"""


async def _serve(route: Route) -> None:
    path: str = route.request.url.removeprefix(_ORIGIN + "/")
    if path == "":
        await route.fulfill(
            body='<!doctype html><link rel="stylesheet" href="/css/app.css"><body></body>', content_type="text/html"
        )
        return
    kind: str = "text/css" if path.endswith(".css") else "text/javascript"
    await route.fulfill(body=(_STATIC / path).read_bytes(), content_type=kind)


async def test_every_icon_draws_about_the_same_area_with_the_same_stroke() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = await browser.new_page()
            await page.route(f"{_ORIGIN}/**", _serve)
            await page.goto(f"{_ORIGIN}/")
            drawn: list[dict[str, float | str]] = await page.evaluate(_MEASURE)
        finally:
            await browser.close()
    assert len(drawn) > 80, len(drawn)
    faults: list[str] = []
    for icon in drawn:
        name, w, h = str(icon["name"]), float(icon["w"]), float(icon["h"])
        if "unread" in icon:
            faults.append(f"{name}: a frame the browser cannot read, {icon['unread']}")
            continue
        cx, cy, stroke = float(icon["cx"]), float(icon["cy"]), float(icon["stroke"])
        # The stroke on the screen: 2 of the 24 grid, at 240 px 20 px, for every icon.
        if abs(stroke - 20.0) > 0.5:
            faults.append(f"{name}: a stroke of {stroke:.1f} px, not 20")
        if icon["cut"]:
            faults.append(f"{name}: its stroke is cut at the frame's edge")
        if name in _OWN_SIZE:
            continue
        area: float = w * h if min(w, h) >= 4 else max(w, h) ** 2
        # A long thin shape (a grip, a line) reaches its longer side's bound before its area: that is its size.
        if not 0.9 * _AREA <= area <= 1.1 * _AREA and max(w, h) < _MOST_SIDE - 1:
            faults.append(f"{name}: draws {w:.1f} by {h:.1f}, not about {_AREA:.0f}")
        if max(w, h) > _MOST_SIDE:
            faults.append(f"{name}: {max(w, h):.1f} long, past {_MOST_SIDE}")
        if abs(cx - 12) > 1 or abs(cy - 12) > 1:
            faults.append(f"{name}: its middle at {cx:.1f}, {cy:.1f}")
    assert faults == [], "\n".join(faults)

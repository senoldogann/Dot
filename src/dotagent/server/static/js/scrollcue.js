// A cue on a box that scrolls sideways (the Models table on a phone): `data-scroll` on it says whether more lies to
// the right ("more"), so the sheet can fade the edge, or not ("end"); the box is read again on scroll and on resize.

const END_PX = 2;

/** Watch `box`; returns `dispose()`. */
export function scrollCue(box) {
  function paint() {
    const more = box.scrollWidth - box.clientWidth - box.scrollLeft > END_PX;
    box.dataset.scroll = more ? "more" : "end";
  }
  const observer = new ResizeObserver(paint);
  observer.observe(box);
  box.addEventListener("scroll", paint, { passive: true });
  paint();
  return () => {
    observer.disconnect();
    box.removeEventListener("scroll", paint);
  };
}

// W15: a row of chips or tabs that scrolls sideways fades at the edge that has more, so a clipped chip says
// "swipe for more" (the phone's filter, status and sub-tab rows)
const SCROLLERS = ".f-scroll, .t-chips, .set-nav, .subtabs, .t-acts";
const EDGE_PX = 2;
let queued = 0;

function fade(el) {
  const more = el.scrollWidth - el.clientWidth > EDGE_PX;
  el.classList.toggle("fade-r", more && el.scrollLeft + el.clientWidth < el.scrollWidth - EDGE_PX);
  el.classList.toggle("fade-l", more && el.scrollLeft > EDGE_PX);
}
function fadeAll() {
  queued = 0;
  for (const el of document.querySelectorAll(SCROLLERS)) fade(el);
}
const later = () => { queued ||= requestAnimationFrame(fadeAll); };

export function initEdges() {
  document.addEventListener("scroll", (e) => { if (e.target.matches?.(SCROLLERS)) fade(e.target); }, true);
  new MutationObserver(later).observe(document.body, { childList: true, subtree: true });
  addEventListener("resize", later);
  later();
}

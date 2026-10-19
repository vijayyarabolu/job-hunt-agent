// One tooltip (hover, focus, long press); data-tip. Lazy.
const HOVER_MS = 300;
const PRESS_MS = 500;
const GAP = 6;
const EDGE = 8;
let timer = 0;
let shown = null;
let armed = null;
let swallow = false;  // the tap ending a long press must not act

const tipEl = () => document.getElementById("tip");
const labelled = (t) => (t instanceof Element ? t.closest("[title], [data-tip]") : null);

function place(el) {
  const tip = tipEl();
  const r = el.getBoundingClientRect();
  const w = tip.offsetWidth;
  const hgt = tip.offsetHeight;
  const above = r.top - hgt - GAP >= EDGE;
  tip.style.left = `${Math.max(EDGE, Math.min(r.left + r.width / 2 - w / 2, innerWidth - w - EDGE))}px`;
  tip.style.top = `${above ? r.top - hgt - GAP : Math.min(r.bottom + GAP, innerHeight - hgt - EDGE)}px`;
}

function show(el, byTouch = false) {
  const text = el.getAttribute("title") || el.dataset.tip || "";
  if (!text.trim()) return;
  if (el.hasAttribute("title")) { el.dataset.tip = text; el.removeAttribute("title"); }
  const tip = tipEl();
  tip.textContent = text;
  tip.hidden = false;
  shown = el;
  swallow = byTouch;
  place(el);
}

// cancel listeners only while a tip is up
const CANCEL = ["touchend", "touchcancel", "touchmove", "pointerdown", "wheel", "scroll"];
const watch = (on) => { for (const ev of CANCEL) document[on ? "addEventListener" : "removeEventListener"](ev, hideTip, { passive: true, capture: true }); };

export function hideTip() {
  clearTimeout(timer);
  timer = 0;
  armed = null;
  watch(false);
  if (shown?.dataset.tip != null && !shown.hasAttribute("title")) shown.setAttribute("title", shown.dataset.tip);
  if (shown) delete shown.dataset.tip;
  shown = null;
  const tip = tipEl();
  if (tip) tip.hidden = true;
}

function arm(el, ms, byTouch = false) {
  if (!el || el === shown || el === armed) return;
  hideTip();
  armed = el;
  watch(true);
  timer = setTimeout(() => show(el, byTouch), ms);
}

export function initTips() {
  if (tipEl()) return;
  const tip = document.createElement("div");
  tip.id = "tip";
  tip.className = "tip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.append(tip);
  // hover means a mouse: a tap also fires pointerover
  const mouse = (e) => e.pointerType === "mouse";
  document.addEventListener("pointerover", (e) => { if (mouse(e)) arm(labelled(e.target), HOVER_MS); });
  document.addEventListener("pointerout", (e) => { if (!mouse(e)) return; const next = labelled(e.relatedTarget); if (next !== (shown || armed)) hideTip(); });
  document.addEventListener("focusin", (e) => { const el = labelled(e.target); if (el?.matches(":focus-visible")) arm(el, HOVER_MS); });
  document.addEventListener("focusout", (e) => { if (labelled(e.target) === (shown || armed)) hideTip(); });
  document.addEventListener("touchstart", (e) => { swallow = false; arm(labelled(e.target), PRESS_MS, true); }, { passive: true });
  // nor the click ending it
  document.addEventListener("click", (e) => { if (swallow) { swallow = false; e.preventDefault(); e.stopPropagation(); } }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });
  addEventListener("resize", hideTip);
}

// Job Hunt Agent Autofill 1.3: the visible result on the page (GOAL27 §6). Every field Autofill touched gets a small mark:
// ✓ filled, ? review (an answer from the resume: read it), ▲ needs you, ✕ failed (the panel's row says why). Blue /
// white / orange / red with a distinct shape each (readable with red-green colour blindness: the shape says it). The marks live in their own layer outside the page's own tree (React never sees them), sit just left of the
// field (never on it: a click always reaches the page), follow it when the page scrolls or moves, and go with it.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const LOOK = {
    filled: ["✓", "Filled", "#1f5fbf", "#fff"],
    review: ["?", "Review: answered from your resume", "#fff", "#1f5fbf"],
    needs: ["▲", "Needs you", "#a85800", "#fff"],
    failed: ["✕", "Did not stick", "#7f1d1d", "#fff"],
  };
  const SIZE = 16;
  const marks = new Map();                       // element -> { node, status, anchor }
  let layer = null, frame = 0;

  function ensureLayer() {
    if (layer && layer.isConnected) return layer;
    layer = document.createElement("div");
    layer.setAttribute("data-jh-marks", "");
    layer.setAttribute("aria-hidden", "true");
    layer.style.cssText = "position:absolute;left:0;top:0;width:0;height:0;z-index:2147483646;pointer-events:none;";
    document.documentElement.append(layer);
    return layer;
  }

  const anchorOf = (el) => (JH.visible(el) ? el : el.closest("label, [class*=field i], fieldset, .form-group") || el.parentElement || el);

  function place() {
    frame = 0;
    for (const [el, m] of marks) {
      if (!el.isConnected) { m.node.remove(); marks.delete(el); continue; }
      const r = (m.anchor && m.anchor.isConnected ? m.anchor : anchorOf(el)).getBoundingClientRect();
      const hidden = r.width === 0 && r.height === 0;
      m.node.style.display = hidden ? "none" : "flex";
      const outside = r.left >= SIZE + 6;              // left of the field; a field at the page's edge: its top left corner
      m.node.style.left = `${Math.round((outside ? r.left - SIZE - 4 : r.left) + scrollX)}px`;
      m.node.style.top = `${Math.round((outside ? r.top + Math.min(r.height, 32) / 2 - SIZE / 2 : r.top - SIZE - 2) + scrollY)}px`;
    }
  }
  const soon = () => { if (!frame) frame = requestAnimationFrame(place); };
  addEventListener("scroll", soon, { passive: true, capture: true });
  addEventListener("resize", soon, { passive: true });
  setInterval(() => { if (marks.size) place(); }, 1000);

  JH.marks = {
    // status: filled | review | needs | failed; anything else clears the field's mark
    set(el, status, why = "", anchor = null) {
      if (!el) return;
      const look = LOOK[status];
      const old = marks.get(el);
      if (!look) { if (old) { old.node.remove(); marks.delete(el); } return; }
      const node = old ? old.node : document.createElement("span");
      node.textContent = look[0];
      node.title = `job-hunt-agent Autofill: ${look[1]}${why ? ` (${why})` : ""}`;
      node.setAttribute("data-jh-mark", status);
      node.style.cssText = `position:absolute;display:flex;align-items:center;justify-content:center;width:${SIZE}px;height:${SIZE}px;` +
        `border-radius:50%;font:700 11px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:${look[2]};color:${look[3]};` +
        `border:1.5px solid ${look[2] === "#fff" ? look[3] : "#fff"};box-shadow:0 1px 3px rgba(0,0,0,.3);pointer-events:none;`;
      if (!old) {
        ensureLayer().append(node);
        marks.set(el, { node, status, anchor });
      } else Object.assign(old, { status, anchor: anchor || old.anchor });
      soon();
    },
    get: (el) => (marks.get(el) || {}).status || null,
    count(status) { let n = 0; for (const m of marks.values()) if (m.status === status) n++; return n; },
    clear() { for (const m of marks.values()) m.node.remove(); marks.clear(); },
  };
})();

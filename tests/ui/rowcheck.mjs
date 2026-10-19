// page-side layout check for the job list rows, evaluated in the browser by the UI drivers (goal9_b.mjs).
// window.__rowCheck(selector) returns one line per problem: two visible leaf elements of a row whose boxes intersect
// (containment of a child in its parent is intended and skipped; the match ring is one drawn unit), a leaf outside
// its row's box (the row clips with contain: paint), text clipped where no ellipsis / line clamp is intended, and a
// one-line element wrapped onto two or more lines.
export const ROW_CHECK = `
(() => {
  const UNITS = ".ring";
  const vis = (el) => { const c = getComputedStyle(el); return c.display !== "none" && c.visibility !== "hidden" && c.opacity !== "0"; };
  const leaves = (root) => {
    const out = [];
    const walk = (el) => {
      if (!vis(el)) return;
      if (el.matches(UNITS) || el.tagName === "svg") { out.push(el); return; }
      const kids = [...el.children].filter(vis);
      const hasText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
      if (el !== root && (!kids.length || hasText)) out.push(el);
      kids.forEach(walk);
    };
    walk(root);
    return out;
  };
  const box = (el) => el.getBoundingClientRect();
  const hit = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
  const name = (el) => {
    const cls = typeof el.className === "string" && el.className.trim() ? "." + el.className.trim().split(/\\s+/).join(".") : "";
    const text = el.textContent.trim();
    return el.tagName.toLowerCase() + cls + (text ? ' "' + text.slice(0, 24) + '"' : "");
  };
  const clamped = (cs) => cs.textOverflow === "ellipsis" || (cs.webkitLineClamp && cs.webkitLineClamp !== "none");
  const lineOf = (cs) => parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.45;
  const checkOne = (row, rb, a, r, issues) => {
    const cs = getComputedStyle(a);
    const where = row.id + ": " + name(a);
    if (r.left < rb.left - 1 || r.right > rb.right + 1 || r.top < rb.top - 1 || r.bottom > rb.bottom + 1) issues.push(where + " outside the row box");
    if (cs.overflow !== "visible" && !clamped(cs) && (a.scrollWidth > a.clientWidth + 1 || a.scrollHeight > a.clientHeight + 1)) issues.push(where + " clipped");
    if (!clamped(cs) && !a.matches(UNITS) && a.textContent.trim() && r.height > lineOf(cs) * 1.9) issues.push(where + " wraps to " + Math.round(r.height / lineOf(cs)) + " lines");
  };
  window.__rowCheck = (sel = "#list .row[data-i]") => {
    const issues = [];
    for (const row of document.querySelectorAll(sel)) {
      const rb = box(row);
      const ls = leaves(row);
      const rects = ls.map(box);
      ls.forEach((a, i) => {
        if (!rects[i].width || !rects[i].height) return;
        checkOne(row, rb, a, rects[i], issues);
        for (let j = i + 1; j < ls.length; j++) {
          const b = ls[j];
          if (a.contains(b) || b.contains(a) || !rects[j].width || !rects[j].height) continue;
          if (hit(rects[i], rects[j])) issues.push(row.id + ": " + name(a) + " overlaps " + name(b));
        }
      });
    }
    return issues;
  };
})();
`;

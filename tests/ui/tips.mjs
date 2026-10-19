// GOAL9 §6 / Done-when 5: every interactive element shows a hover label. Shared by the goal9 area drivers.
// missingTips(js, scope) -> the interactive elements inside `scope` (a CSS selector, default the whole page) that are
// visible and have neither a title nor a parked data-tip; [] means the view passes. Pass extra selectors to ignore
// (e.g. plain text inputs that label themselves with a placeholder) through `ignore`.
export const INTERACTIVE = "button, a[href], [role=option], [role=tab], [role=menuitem], summary, select, input, textarea, .chip, .tag, .rgc, .pick, .ring, [tabindex]:not([tabindex='-1'])";
export const missingTips = (js, scope = "body", ignore = "") => js(`
  const seen = new Set();
  return [...document.querySelectorAll(${JSON.stringify(scope)})].flatMap((root) => [root, ...root.querySelectorAll(${JSON.stringify(INTERACTIVE)})])
    .filter((el) => el.matches(${JSON.stringify(INTERACTIVE)}) && el.offsetParent !== null && !seen.has(el) && seen.add(el)
      && !(${JSON.stringify(ignore)} && el.matches(${JSON.stringify(ignore || "x")}))
      && !(el.getAttribute("title") || el.dataset.tip || "").trim())
    .map((el) => el.outerHTML.replace(/\\s+/g, " ").replace(/ (id|data-i|aria-selected|aria-checked|data-uid)="[^"]*"/g, "").slice(0, 110))
    .filter((sig, i, all) => all.indexOf(sig) === i);`);   // one line per kind of control, not one per row
// hovering: dispatch a real mouse move over the element and wait for #tip; returns the tip text or "" (and whether
// the tip overlaps the control, which must be false)
export const hoverTip = async (send, js, sleep, sel) => {
  const r = await js(`const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return null; const b = el.getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2, l: b.left, t: b.top, r: b.right, b: b.bottom };`);
  if (!r) return { text: "", overlaps: false, found: false };
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: r.x, y: r.y });
  await sleep(450);
  return js(`const t = document.getElementById("tip"); if (!t || t.hidden) return { text: "", overlaps: false, found: true };
    const b = t.getBoundingClientRect(); const o = !(b.right <= ${r.l} || b.left >= ${r.r} || b.bottom <= ${r.t} || b.top >= ${r.b});
    return { text: t.textContent, overlaps: o, found: true };`);
};

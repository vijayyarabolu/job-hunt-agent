// Job Hunt Agent Autofill: the page-world helper (MAIN world). The content script marks an element and names an
// operation; this calls the element's own React handler, which the isolated content script cannot see.
// Used only when a plain DOM event did not take (Workday's dropdown options, date parts, resume drop zone).
(() => {
  "use strict";
  if (window.__jhBridge) return;
  window.__jhBridge = true;
  const props = (el) => {
    const key = Object.keys(el).find((k) => k.startsWith("__reactProps$"));
    return key ? el[key] : null;
  };
  const noop = () => {};
  const OPS = {
    click: (el) => { const p = props(el); if (!p || !p.onClick) return false; p.onClick({ preventDefault: noop, stopPropagation: noop, target: el, currentTarget: el }); return true; },
    keyTab: (el, arg) => { const p = props(el); if (!p || !p.onKeyDown) return false; p.onKeyDown({ key: "Tab", keyCode: 9, target: { value: arg }, preventDefault: noop, stopPropagation: noop }); return true; },
    keyEnter: (el, arg) => { const p = props(el); if (!p || !p.onKeyDown) return false; p.onKeyDown({ key: "Enter", keyCode: 13, target: { value: arg }, currentTarget: el, preventDefault: noop, stopPropagation: noop }); return true; },
    dateUp: (el, arg) => {
      const p = props(el); if (!p || !p.onKeyDown) return false;
      const value = String(parseInt(arg, 10) - 1);
      p.onKeyDown({ key: "ArrowUp", nativeEvent: { key: "Up", setSelectionRange: noop }, preventDefault: noop,
                    currentTarget: { value, setSelectionRange: noop }, target: el });
      return true;
    },
    drop: (el) => {
      const p = props(el); const input = el.querySelector("input[type=file]") || document.querySelector("input[type=file][data-jh-filled]");
      if (!p || !p.onDrop || !input || !input.files || !input.files.length) return false;
      p.onDrop({ dataTransfer: { files: input.files, items: [], types: ["Files"] }, preventDefault: noop, stopPropagation: noop });
      return true;
    },
  };
  document.addEventListener("jh-bridge", (e) => {
    const [tag, op] = String(e.detail || "").split("|");
    const el = document.querySelector(`[data-jh-bridge="${tag}"]`);
    let ok = false;
    try { ok = Boolean(el && OPS[op] && OPS[op](el, el.getAttribute("data-jh-bridge-arg") || "")); } catch { ok = false; }
    if (el) el.setAttribute("data-jh-bridge-result", ok ? "ok" : "no");
    document.dispatchEvent(new CustomEvent("jh-bridge-done", { detail: tag }));
  });
})();

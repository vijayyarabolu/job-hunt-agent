// Job Hunt Agent Autofill, content script: GOAL25 §5 "Open next 5". Only on the Job Hunt Agent page itself (the address saved in
// Settings): it says the extension is here, and passes the page's list of apply pages to the service worker, which
// opens each in a new tab that already knows its job. A page alone may open only one tab per click.
"use strict";

(() => {
  if (window.top !== window) return;
  chrome.storage.local.get("base").then(({ base }) => {
    let own = "";
    try { own = new URL(base).origin; } catch { return; }
    if (location.origin !== own) return;
    const hello = () => window.postMessage({ type: "jh:ext", version: chrome.runtime.getManifest().version }, own);
    window.addEventListener("message", async (e) => {
      if (e.source !== window || e.origin !== own || !e.data) return;
      if (e.data.type === "jh:ping") return hello();
      if (e.data.type !== "jh:open") return;
      let got = null;
      try { got = await chrome.runtime.sendMessage({ type: "queue:open", items: e.data.items }); } catch { got = null; }
      window.postMessage({ type: "jh:opened", opened: (got && got.ok && got.data.opened) || 0, error: got && !got.ok ? got.error : null }, own);
    });
    hello();
  });
})();

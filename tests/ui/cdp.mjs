// Headless Chrome over the DevTools protocol, no packages: the harness shared by the UI drivers (goal7_ui, goal8_ui).
// start(chromePath, profileDir) -> helpers; every driver prints {"checks": [{name, ok, detail}]} for its Python test.
// Downloads go to <profileDir>/downloads (the test's temp dir), never ~/Downloads, and are deleted on close().
import { spawn } from "node:child_process";
import { rmSync } from "node:fs";
import { join } from "node:path";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const KEYS = { Escape: 27, Enter: 13, ArrowDown: 40, Tab: 9, " ": 32 };

export async function start(chromePath, profileDir) {
  const port = 9300 + Math.floor(Math.random() * 600);
  const chrome = spawn(chromePath, ["--headless=new", "--hide-scrollbars", `--remote-debugging-port=${port}`,
    `--user-data-dir=${profileDir}`, "--no-first-run", ...(process.getuid && process.getuid() === 0 ? ["--no-sandbox"] : []), "about:blank"], { stdio: "ignore" });
  // a timed-out test SIGKILLs this driver, so close() never runs and Chrome lived on for days (ten found,
  // parent launchd). A shell watches this process and stops Chrome once it is gone, however it died.
  spawn("/bin/sh", ["-c", `while kill -0 ${process.pid} 2>/dev/null; do sleep 1; done; kill ${chrome.pid} 2>/dev/null`],
    { stdio: "ignore", detached: true }).unref();
  let targets;
  for (let i = 0; i < 100 && !targets; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); } catch { await sleep(150); }
  }
  const ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r));
  let seq = 0;
  const pending = new Map();
  const errors = [];
  const checks = [];
  const state = { failNext: null, holdNext: null };   // failNext: a URL part whose next request the "server" refuses;
                                                       // holdNext: { url, body, ms }: that request reaches the server ms late
  const send = (method, params = {}) => new Promise((r) => { const i = ++seq; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result || m.error); pending.delete(m.id); }
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    if (m.method === "Fetch.requestPaused") {
      const { url, postData = "" } = m.params.request;
      const hold = state.holdNext;
      if (hold && url.includes(hold.url) && postData.includes(hold.body)) {
        state.holdNext = null;
        setTimeout(() => send("Fetch.continueRequest", { requestId: m.params.requestId }), hold.ms);
        return;
      }
      const hit = state.failNext && m.params.request.url.includes(state.failNext);
      if (hit) state.failNext = null;
      send(hit ? "Fetch.fulfillRequest" : "Fetch.continueRequest", hit ? { requestId: m.params.requestId, responseCode: 500,
        responseHeaders: [{ name: "Content-Type", value: "application/json" }], body: btoa('{"error":"server said no"}') }
        : { requestId: m.params.requestId });
    }
  });
  const downloads = join(profileDir, "downloads");
  const set = await send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: downloads });
  if (set?.code) { chrome.kill(); throw new Error(`downloads not redirected to the temp dir: ${set.message}`); }
  const js = async (expr) => (await send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true }))?.result?.value;
  const until = async (expr, ms = 6000) => { for (let t = 0; t < ms; t += 100) { if (await js(`return !!(${expr})`)) return true; await sleep(100); } return false; };
  async function key(k, modifiers = 0) {
    const code = KEYS[k] || k.toUpperCase().charCodeAt(0);
    const text = k.length === 1 && !modifiers ? k : undefined;
    await send("Input.dispatchKeyEvent", { type: "keyDown", key: k, windowsVirtualKeyCode: code, modifiers, text });
    await send("Input.dispatchKeyEvent", { type: "keyUp", key: k, windowsVirtualKeyCode: code, modifiers });
    await sleep(150);
  }
  return {
    send, js, until, key, errors, checks, state, downloads,
    check: (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: String(detail) }),
    click: (sel) => js(`const el = [...document.querySelectorAll(${JSON.stringify(sel)})].find((x) => x.offsetParent !== null); el?.click(); return !!el`),
    clickText: (sel, text) => js(`const el = [...document.querySelectorAll(${JSON.stringify(sel)})].find((x) => x.textContent.trim().startsWith(${JSON.stringify(text)})); el?.click(); return !!el`),
    server: (path) => js(`return (await (await fetch(${JSON.stringify(path)})).json())`),
    close: () => { ws.close(); chrome.kill(); rmSync(downloads, { recursive: true, force: true }); },
  };
}

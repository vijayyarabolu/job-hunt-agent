// headless Chrome over --remote-debugging-pipe (no packages), the only way Chrome loads an unpacked
// extension for a test (Extensions.loadUnpacked). Pages of a fake board host are answered from local fixture files
// (Fetch domain); every other outside request is failed, so a test never reaches a real site.
import { spawn } from "node:child_process";
import { readFileSync, rmSync } from "node:fs";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function launch(chromePath, profileDir) {
  const chrome = spawn(chromePath, ["--headless=new", "--remote-debugging-pipe", "--enable-unsafe-extension-debugging",
    `--user-data-dir=${profileDir}`, "--no-first-run", "--no-default-browser-check", "--window-size=1400,1000", "about:blank"],
    { stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"] });
  spawn("/bin/sh", ["-c", `while kill -0 ${process.pid} 2>/dev/null; do sleep 1; done; kill ${chrome.pid} 2>/dev/null`],
    { stdio: "ignore", detached: true }).unref();
  const out = chrome.stdio[3], inp = chrome.stdio[4];
  let seq = 0, buf = "";
  const pending = new Map(), listeners = [];
  inp.on("data", (chunk) => {
    buf += chunk.toString();
    let i;
    while ((i = buf.indexOf("\0")) >= 0) {
      const m = JSON.parse(buf.slice(0, i));
      buf = buf.slice(i + 1);
      if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } else for (const l of listeners) l(m);
    }
  });
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, (m) => (m.error ? reject(new Error(`${method}: ${m.error.message}`)) : resolve(m.result)));
    out.write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + "\0");
  });
  const on = (fn) => listeners.push(fn);
  const downloads = `${profileDir}/downloads`;           // never ~/Downloads: the test's temp dir, deleted on close
  const set = await send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: downloads });
  if (set && set.code) { chrome.kill(); throw new Error(`downloads not redirected to the temp dir: ${set.message}`); }
  return { chrome, send, on, close: () => { chrome.kill(); try { rmSync(profileDir, { recursive: true, force: true }); } catch { /* gone */ } } };
}

// One tab: helpers bound to its session.
// brings a node into view, also inside a scrolling list in a shadow root (where scrollIntoView may not reach)
export const SHOW = "this.scrollIntoView({ block: 'center', behavior: 'instant' }); { const r0 = this.getBoundingClientRect(); if (r0.top < 0 || r0.bottom > innerHeight) " +
  "for (let n = this.parentNode; n; n = n.parentNode || n.host) { if (n instanceof Element && n.scrollHeight > n.clientHeight + 2 && /auto|scroll/.test(getComputedStyle(n).overflowY)) " +
  "{ n.scrollTop += r0.top - innerHeight / 2; break; } } }";
export async function tab(b, url = "about:blank") {
  const { targetId } = await b.send("Target.createTarget", { url });
  const { sessionId } = await b.send("Target.attachToTarget", { targetId, flatten: true });
  const send = (m, p) => b.send(m, p, sessionId);
  const errors = [], logs = [];
  b.on((m) => {
    if (m.sessionId !== sessionId) return;
    if (m.method === "Runtime.exceptionThrown") errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    if (m.method === "Runtime.consoleAPICalled" && /warn|error/.test(m.params.type)) {
      logs.push(m.params.args.map((a) => a.value ?? a.description ?? a.type).join(" ").slice(0, 300));
    }
  });
  await send("Runtime.enable");
  await send("Page.enable");
  await send("DOM.enable");
  const js = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  const until = async (expr, ms = 8000) => { for (let t = 0; t < ms; t += 120) { try { if (await js(`return !!(${expr})`)) return true; } catch { /* navigating */ } await sleep(120); } return false; };
  async function clickAt(x, y) {                        // a person's click: trusted mouse events
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
    await sleep(150);
  }
  async function clickSel(sel) {
    const r = await js(`const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return null; el.scrollIntoView({block: "center"}); const b = el.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]`);
    if (!r) return false;
    await clickAt(r[0], r[1]);
    return true;
  }
  // the panel lives in a closed shadow root: found through the DOM domain (pierce), clicked like a person
  async function panelNode() {
    const { root } = await send("DOM.getDocument", { depth: -1, pierce: true });
    const find = (n) => {
      if (n.attributes && n.attributes.includes("data-jh-panel")) return n;
      for (const k of [...(n.children || []), ...(n.shadowRoots || [])]) { const f = find(k); if (f) return f; }
      return null;
    };
    return { root, host: find(root) };
  }
  async function panelText() {
    const { host } = await panelNode();
    if (!host || !host.shadowRoots) return "";
    const { outerHTML } = await send("DOM.getOuterHTML", { backendNodeId: host.shadowRoots[0].backendNodeId });
    return outerHTML.replace(/<style>[\s\S]*?<\/style>/, "").replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();
  }
  async function panelClick(text, tries = 4) {
    const { host } = await panelNode();
    if (!host) return false;
    const walk = (n) => {
      if (["BUTTON", "A", "SPAN"].includes(n.nodeName) && (n.children || []).some((c) => c.nodeType === 3 && c.nodeValue.trim().startsWith(text))) return n;
      if (n.nodeName === "LABEL" && (n.children || []).some((c) => c.nodeType === 3 && c.nodeValue.trim().startsWith(text))) {
        return (n.children || []).find((c) => c.nodeName === "INPUT") || n;
      }
      for (const k of [...(n.children || []), ...(n.shadowRoots || [])]) { const f = walk(k); if (f) return f; }
      return null;
    };
    const btn = walk(host);
    if (!btn) return false;
    return (await clickNode(btn.backendNodeId)) || (tries > 1 && panelClick(text, tries - 1));   // the panel redrew: find it again
  }
  // scrolled into view first (a long panel list scrolls), then clicked where it is on screen now
  async function clickNode(backendNodeId) {
    let object;
    try { ({ object } = await send("DOM.resolveNode", { backendNodeId })); } catch { return false; }
    const { result } = await send("Runtime.callFunctionOn", { objectId: object.objectId, returnByValue: true,
      functionDeclaration: `function () { if (!this.isConnected) return null; ${SHOW} const r = this.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }` });
    if (!result.value) return false;                  // gone (re-rendered) since it was found
    await clickAt(result.value[0], result.value[1]);
    return true;
  }
  return { targetId, sessionId, send, js, until, errors, logs, clickAt, clickSel, clickNode, panelText, panelClick };
}

// Answer requests to `host` (a name or a list) from files: route(url) -> {file, type} | null (null: 404). Everything else fails.
export async function serveFixtures(t, b, host, route) {
  await t.send("Fetch.enable", { patterns: [{ urlPattern: "*", requestStage: "Request" }] });
  b.on(async (m) => {
    if (m.sessionId !== t.sessionId || m.method !== "Fetch.requestPaused") return;
    const { requestId, request } = m.params;
    const u = new URL(request.url);
    if (![].concat(host).includes(u.hostname)) return t.send("Fetch.failRequest", { requestId, errorReason: "BlockedByClient" }).catch(() => {});
    const hit = route(u);
    if (!hit) return t.send("Fetch.fulfillRequest", { requestId, responseCode: 404, body: "" }).catch(() => {});
    t.send("Fetch.fulfillRequest", { requestId, responseCode: 200, responseHeaders: [{ name: "Content-Type", value: hit.type }],
      body: readFileSync(hit.file).toString("base64") }).catch(() => {});
  });
}

// dol.gov answers curl / urllib with "Access Denied" (its bot shield checks the browser), so the public
// OFLC disclosure files are fetched by headless Chrome. No packages: Node's own WebSocket + the DevTools protocol.
// usage: node dolfetch.mjs <chrome path> <profile dir> <out dir> list            -> prints {"links": [url, ...]}
//        node dolfetch.mjs <chrome path> <profile dir> <out dir> get <url> ...    -> prints {"saved": [{url, file, bytes}]}
// Files land in <out dir> (data/h1b), never ~/Downloads; the Chrome profile dir is deleted on exit.
import { spawn } from "node:child_process";
import { existsSync, readdirSync, renameSync, rmSync, statSync } from "node:fs";
import { basename, join } from "node:path";

const PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance";
const DOWNLOAD_TIMEOUT_MS = 30 * 60 * 1000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const [CHROME, PROFILE, OUT, MODE, ...URLS] = process.argv.slice(2);

const port = 9900 + Math.floor(Math.random() * 90);
const chrome = spawn(CHROME, ["--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${PROFILE}`,
  "--no-first-run", "about:blank"], { stdio: "ignore" });
spawn("/bin/sh", ["-c", `while kill -0 ${process.pid} 2>/dev/null; do sleep 1; done; kill ${chrome.pid} 2>/dev/null`],
  { stdio: "ignore", detached: true }).unref();

let ws;
let seq = 0;
const pending = new Map();
const events = [];
const send = (method, params = {}) => new Promise((r) => {
  const i = ++seq; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params }));
});
const js = async (expr) => (await send("Runtime.evaluate",
  { expression: `(async () => { ${expr} })()`, awaitPromise: true, returnByValue: true }))?.result?.value;

async function connect() {
  let targets;
  for (let i = 0; i < 100 && !targets; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); } catch { await sleep(150); }
  }
  ws = new WebSocket(targets.find((t) => t.type === "page").webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r));
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result || m.error); pending.delete(m.id); }
    if (m.method) events.push(m);
  });
  await send("Page.enable");
  await send("Browser.setDownloadBehavior", { behavior: "allowAndName", downloadPath: OUT, eventsEnabled: true });
}

async function openPage() {
  await send("Page.navigate", { url: PAGE });
  for (let t = 0; t < 60; t++) {
    if (await js("return document.querySelectorAll('a[href*=\"Disclosure\"]').length > 10")) return true;
    await sleep(500);
  }
  return false;
}

async function download(url) {
  const name = basename(new URL(url).pathname);
  const done = join(OUT, name);
  const since = events.length;
  await send("Page.navigate", { url });
  const started = Date.now();
  let guid = null;
  while (Date.now() - started < DOWNLOAD_TIMEOUT_MS) {
    for (const m of events.slice(since)) {
      if (m.method === "Browser.downloadWillBegin" && !guid) guid = m.params.guid;
      if (m.method === "Browser.downloadProgress" && m.params.guid === guid && m.params.state === "completed") {
        const file = join(OUT, guid);
        if (existsSync(file)) renameSync(file, done);
        return { url, file: done, bytes: statSync(done).size };
      }
      if (m.method === "Browser.downloadProgress" && m.params.guid === guid && m.params.state === "canceled") {
        return { url, error: "download canceled" };
      }
    }
    await sleep(500);
  }
  return { url, error: "timed out" };
}

try {
  await connect();
  if (!await openPage()) throw new Error("the DOL performance page did not load (blocked or changed)");
  if (MODE === "list") {
    const links = await js("return [...new Set([...document.querySelectorAll('a[href]')].map((a) => a.href)" +
      ".filter((h) => /(LCA|PERM)_Dis[a-z]*_Data_[^/]*\\.xlsx$/i.test(h)))]");
    console.log(JSON.stringify({ links }));
  } else {
    const saved = [];
    for (const url of URLS) saved.push(await download(url));
    for (const f of readdirSync(OUT)) if (/^[0-9a-f-]{36}$/.test(f)) rmSync(join(OUT, f), { force: true });
    console.log(JSON.stringify({ saved }));
  }
} catch (err) {
  console.log(JSON.stringify({ error: String(err.message || err) }));
} finally {
  ws?.close();
  chrome.kill();
  await sleep(300);
  rmSync(PROFILE, { recursive: true, force: true });
}

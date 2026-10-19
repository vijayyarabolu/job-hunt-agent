// the nightly canary. The real extension in headless Chrome, a fake person (job-hunt-agent's canary profile on a
// throwaway database), every saved fixture (served from disk at its board's own address, so the right adapter runs)
// and a few live public apply pages. Autofill is clicked once per page; the panel's counts are the result. Never
// submits: on a live page every request that is not a GET is blocked, and Autofill itself never clicks Next / Submit.
// usage: node scripts/autofill/canary.mjs <job-hunt-agent base> <token> <chrome> <profile dir> <extension dir> <fixtures dir> <pages.json>
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { launch, sleep, tab } from "../../tests/ui/extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXT_DIR, FIX, PAGES] = process.argv.slice(2);
const PID = "canary";
const FORM = "document.querySelectorAll('input:not([type=hidden]), select, textarea').length >= 3";
const APPLY = /^(apply( now| for this (job|position))?|i'?m interested)$/i;
const COUNTS = /Filled (\d+) · Needs you (\d+) · Skipped (\d+)/;
const LOAD_MS = 25000;
const FILL_MS = 150000;
const COVER_MS = 4000;
const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

const pages = JSON.parse(readFileSync(PAGES, "utf8"));
const out = [];

function workdaySim(u) {                               // the Workday simulator the GOAL23 tests use
  if (u.pathname === "/__sim/workday_sim.js") return { file: join(FIX, "workday_sim.js"), type: "text/javascript" };
  if (u.pathname.startsWith("/__sim/steps/")) return { file: join(FIX, "workday", "steps", u.pathname.split("/").pop()), type: "text/html" };
  return { file: join(FIX, "workday", `${u.searchParams.get("fx") || "job"}.html`), type: "text/html" };
}

// a fixture: its board's own host answered from disk, everything else failed. A live page: anything but a GET blocked.
// a live page may read (GET, or a GraphQL query sent as POST: Ashby loads its form that way), never write: a GraphQL
// mutation (Ashby's submit), a form post or any other POST is blocked, so nothing can ever be submitted
function readOnly(request) {
  if (SAFE.has(request.method)) return true;
  if (request.method !== "POST" || !request.postData) return false;
  let body;
  try { body = JSON.parse(request.postData); } catch { return false; }
  const ops = Array.isArray(body) ? body : [body];
  return ops.length > 0 && ops.every((op) => op && typeof op.query === "string" && /^\s*(query\b|\{)/.test(op.query.replace(/^\s*#.*$/gm, ""))
    && !/\b(mutation|subscription)\b/i.test(op.query));     // a document holding a mutation anywhere is a write
}

async function intercept(b, t, page) {
  const host = new URL(page.url).hostname;
  await t.send("Fetch.enable", { patterns: [{ urlPattern: "*", requestStage: "Request" }] });
  b.on((m) => {
    if (m.sessionId !== t.sessionId || m.method !== "Fetch.requestPaused") return;
    const { requestId, request } = m.params;
    const fail = () => t.send("Fetch.failRequest", { requestId, errorReason: "BlockedByClient" }).catch(() => {});
    if (page.source === "live") return readOnly(request) ? t.send("Fetch.continueRequest", { requestId }).catch(() => {}) : fail();
    const u = new URL(request.url);
    if (u.hostname !== host) return fail();
    const hit = page.sim === "workday" ? workdaySim(u) : u.pathname === new URL(page.url).pathname ? { file: page.file, type: "text/html" } : null;
    if (!hit) return t.send("Fetch.fulfillRequest", { requestId, responseCode: 404, body: "" }).catch(() => {});
    t.send("Fetch.fulfillRequest", { requestId, responseCode: 200, responseHeaders: [{ name: "Content-Type", value: hit.type }],
      body: readFileSync(hit.file).toString("base64") }).catch(() => {});
  });
}

async function extWorld(b, t) {                        // the extension's content-script world in the page
  let ctx = null;
  const frame = (await t.send("Page.getFrameTree")).frameTree.frame.id;
  b.on((m) => {
    if (m.sessionId !== t.sessionId || m.method !== "Runtime.executionContextCreated") return;
    const c = m.params.context;
    if (c.name === "job-hunt-agent Autofill" && c.auxData && c.auxData.frameId === frame && c.auxData.type === "isolated") ctx = c.id;
  });
  return async (expr) => {
    for (let i = 0; i < 50 && !ctx; i++) await sleep(100);
    if (!ctx) throw new Error("the extension did not start on this page");
    const r = await t.send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, contextId: ctx, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
}

async function one(b, page) {
  const t = await tab(b);
  const result = { name: page.name, board: page.board, source: page.source, step: page.step || "", expected: page.expected ?? null,
    fields: 0, filled: 0, needs: 0, skipped: 0 };
  try {
    await intercept(b, t, page);
    const ext = await extWorld(b, t);
    await t.send("Page.navigate", { url: page.url });
    await t.until("document.readyState === 'complete'", LOAD_MS);
    if (page.source === "live" && !(await t.until(FORM, 8000))) {
      await t.js(`const a = [...document.querySelectorAll("a, button")].find((x) => ${APPLY}.test((x.innerText || "").trim())); if (a) a.click(); return !!a;`);
      await t.until(FORM, 12000);
    }
    await sleep(2500);
    if (!(await t.js(`return ${FORM}`))) return { ...result, skip: "no application form on the page (posting closed?)" };
    await ext("if (!JH.panel.shown()) JH.top.message({ type: 'togglePanel' }); return true");
    await sleep(1500);
    if (!(await t.panelClick("Autofill"))) throw new Error("the Autofill button was not found in the panel");
    let covered = false;
    await sleep(COVER_MS);
    const started = await t.panelText();
    if (!started.includes("Filling…") && !COUNTS.test(started)) {   // the page's own overlay took the click
      covered = true;
      await ext("JH.top.autofill(); return true");
    }
    let text = "";
    for (let i = 0; i < FILL_MS / 500; i++) {
      await sleep(500);
      text = await t.panelText();
      if (COUNTS.test(text) && !text.includes("Filling…")) break;
    }
    const m = COUNTS.exec(text);
    if (!m) throw new Error(`no result in the panel: ${text.slice(0, 200)}`);
    const [filled, needs, skipped] = m.slice(1).map(Number);
    const tripped = /tripped Autofill|hit a problem/.test(text) ? "an adapter error (the plain filler took over)" : "";
    return { ...result, filled, needs, skipped, fields: filled + needs + skipped, ...(tripped ? { error: tripped } : {}),
      ...(covered ? { note: "the page covered the Autofill button" } : {}) };
  } catch (err) {
    return { ...result, error: String((err && err.message) || err).slice(0, 300) };
  } finally {
    await b.send("Target.closeTarget", { targetId: t.targetId }).catch(() => {});
  }
}

const b = await launch(CHROME, PROFILE_DIR);
try {
  const { id } = await b.send("Extensions.loadUnpacked", { path: EXT_DIR });
  const o = await tab(b, `chrome-extension://${id}/options.html`);
  await o.until("document.getElementById('base')");
  await o.js(`const $ = (i) => document.getElementById(i);
    $("base").value = ${JSON.stringify(BASE)}; $("token").value = ${JSON.stringify(TOKEN)};
    const seg = document.querySelector(".seg");
    if (!document.querySelector("input[name=profile][value=${PID}]")) seg.append(Object.assign(document.createElement("input"), { type: "radio", name: "profile", value: "${PID}" }));
    document.querySelector("input[name=profile][value=${PID}]").checked = true;
    $("email").value = "canary@example.com"; $("password").value = "Canary-not-a-real-password-1"; if ($("auto")) $("auto").checked = false;
    $("form").requestSubmit(); return true`);
  await sleep(1500);                                   // the options page may reload after Save
  await o.until("document.readyState === 'complete'", 10000);
  const saved = await o.js(`await chrome.storage.local.set({ auto: false }); return (await chrome.storage.local.get(["base", "profile"]))`);
  if (saved.base !== BASE || saved.profile !== PID) throw new Error(`the extension's settings were not saved: ${JSON.stringify(saved)}`);
  for (const page of pages) out.push(await one(b, page));
} catch (err) {
  out.push({ name: "runner", board: "?", source: "runner", fields: 0, filled: 0, error: String((err && err.stack) || err).slice(0, 500) });
} finally {
  b.close();
  console.log(JSON.stringify({ pages: out }));
}

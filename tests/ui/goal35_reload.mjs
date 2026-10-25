// GOAL35 §1 (tests/test_goal35_reload_ui.py): on every page, set the filters / sort / segment / search / job by hand,
// then reload 3 times: each reload shows the same page with the same content and counts as the hand-made view.
// mode "record" does that and writes each page's address and view to <out>; mode "replay" (after the server was
// restarted, the service worker installed) opens each saved address as a copied link and compares.
// usage: node goal35_reload.mjs <base url> <token> <chrome path> <profile dir> <record|replay> <out.json> [other person id] [first person id]
import { readFileSync, writeFileSync } from "node:fs";
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, MODE, OUT, OTHER = "other", ME = "tester"] = process.argv.slice(2);
const { send, js, until, errors, checks, check, close } = await start(CHROME, PROFILE_DIR);
const SETTLE_MS = 400, SETTLE_MAX_MS = 12000, RELOADS = 3;

const snap = () => js(`
  const t = (s) => (document.querySelector(s)?.innerText || "").replace(/\\b\\d+ ?(s|sec|secs|min|mins|h|hr|hrs|d|m|minutes?|hours?|days?) ago\\b/g, "#ago").replace(/\\s+/g, " ").trim();
  const list = !document.querySelector('#pane-list').hidden, view = !document.querySelector('#view').hidden;
  return JSON.stringify({ hash: location.hash, view: document.body.dataset.view,
    tab: document.querySelector('#tabs [aria-selected=true]')?.dataset.tab || "",
    who: document.querySelector('#who [aria-pressed=true]')?.dataset.person || "",
    head: list ? t('#list-head') : "", filters: list && !document.querySelector('#filters').hidden ? t('#fchips') : "",
    sort: list && !document.querySelector('#filters').hidden ? document.querySelector('#sort [aria-pressed=true]')?.dataset.v || "" : "",
    rows: list ? [...document.querySelectorAll('#list .row[data-i]')].map((r) => r.querySelector('.row-title')?.textContent).join(" ; ") : "",
    sel: list ? document.querySelector('#list .row[aria-selected=true] .row-title')?.textContent || "" : "",
    detail: list ? document.querySelector('#detail .d-title')?.textContent || "" : "",
    page: view ? t('#view').slice(0, 4000) : "",
    search: document.querySelector('#view:not([hidden]) #t-search, #pane-list:not([hidden]) .ap-search')?.value || "" })`);
const busy = "document.querySelector('#pane-list:not([hidden]) #list[aria-busy], #pane-list:not([hidden]) #list .sk, #view:not([hidden]) .placeholder, #view:not([hidden]) .sk-line')";

async function settle() {
  if (process.env.JH_DEBUG) console.error("settle", new Date().toISOString().slice(11, 19));
  await until(`!(${busy})`, SETTLE_MAX_MS);
  let prev = await snap();
  for (let t = 0; t < SETTLE_MAX_MS; t += SETTLE_MS) {
    await sleep(SETTLE_MS);
    const now = await snap();
    if (now === prev && !(await js(`return !!(${busy})`))) return JSON.parse(now);
    prev = now;
  }
  return JSON.parse(prev);
}
const at = (a, b) => { let i = 0; while (i < a.length && a[i] === b[i]) i++; return Math.max(0, i - 60); };
const diff = (a, b) => Object.keys(a).filter((k) => a[k] !== b[k]).map((k) => { const x = String(a[k]), y = String(b[k]), i = at(x, y);
  return `${k}: …${x.slice(i, i + 240)} ≠ …${y.slice(i, i + 240)}`; }).join(" | ");
const tabClick = (tab) => js(`document.querySelector('#tabs [data-tab=${tab}]').click()`);
const typeIn = (sel, text) => js(`const el = document.querySelector(${JSON.stringify(sel)}); el.focus(); el.value = ${JSON.stringify(text)};
  el.dispatchEvent(new Event('input', { bubbles: true })); el.blur()`);
const clickText = (sel, text) => js(`const el = [...document.querySelectorAll(${JSON.stringify(sel)})].find((x) => x.textContent.trim().startsWith(${JSON.stringify(text)})); el?.click(); return !!el`);
const SEARCH = process.env.JH_RELOAD_SEARCH || "acme";

// each page: how the user gets there and what they change by hand
const PAGES = [
  ["Recommended", async () => {
    await tabClick("recommended"); await settle();
    await clickText("#fchips .seg button", "3d"); await settle();
    await js("document.querySelector('#sort [data-v=match]').click()"); await settle();
    await js("const rs = document.querySelectorAll('#list .row[data-i] .row-title'); rs[Math.min(1, rs.length - 1)]?.click()");
  }],
  ["Saved", async () => { await tabClick("saved"); await settle(); await js("document.querySelector('#sort [data-v=match]')?.click()"); }],
  ["Archived", async () => { await tabClick("archived"); }],
  ["Tailored resumes", async () => { await tabClick("resumes"); await settle(); await clickText("#list-head .rq-seg button", "Queue"); }],
  ["Tracker", async () => {
    await tabClick("tracker"); await settle();
    await typeIn("#t-search", SEARCH); await sleep(700); await settle();
    await clickText(".t-chips .chip", "Applied");
  }],
  ["Applied", async () => { await tabClick("applied"); await settle(); await clickText("#list-head .rq-seg button", "Waiting"); await settle();
    await typeIn(".ap-search", SEARCH); await sleep(600); }],
  ["Interviewing", async () => { await tabClick("interviewing"); }],
  ["Follow-ups", async () => { await tabClick("followups"); }],
  ["Stats", async () => { await tabClick("stats"); }],
  ["Answers", async () => { await tabClick("answers"); }],
  ["Settings", async () => { await js("document.querySelector('#settings-btn').click()"); await settle(); await js("document.querySelectorAll('.set-nav [data-sec]')[1]?.click()"); }],
  ["The other person's Recommended", async () => {
    await tabClick("recommended"); await settle();
    await js(`document.querySelector('#who [data-person=${OTHER}]').click()`); await settle();
    await clickText("#fchips .seg button", "24h");
  }],
];

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
  if (MODE === "record") {
    await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended?p=${ME}` });
    await until("document.querySelector('#who [data-person]')", 10000);
    await settle();
    const saved = [];
    for (const [name, setUp] of PAGES) {
      if (process.env.JH_DEBUG) console.error("page", name);
      await setUp();
      const hand = await settle();
      check(`${name}: the address names the page`, hand.hash.startsWith(`#${hand.tab || "settings"}`), hand.hash);
      for (let i = 1; i <= RELOADS; i++) {
        await send("Page.reload");
        await until("document.querySelector('#who [data-person]')", 10000);
        if (name === "Tailored resumes") {        // the first drawn head is already the real board, never a bare "Resumes"
          await until("document.querySelector('#lh-title')", 8000);
          const first = await js("return document.querySelector('#lh-title')?.textContent || ''");
          check(`Tailored resumes: reload ${i} draws the board at once`, first.startsWith("Tailored resumes") && await js("return !!document.querySelector('#list-head .rq-seg')"), first);
        }
        const again = await settle();
        check(`${name}: reload ${i} shows the same page`, JSON.stringify(again) === JSON.stringify(hand), diff(hand, again));
      }
      check(`${name}: the service worker controls the page`, await js("return !!navigator.serviceWorker?.controller"), "no controller");
      saved.push({ name, hand });
    }
    // back / forward: back from the Tracker shows Recommended with its state
    await send("Page.navigate", { url: `${BASE}/${saved[0].hand.hash}` });
    await settle();
    await js(`document.querySelector('#who [data-person=${ME}]')?.click()`);
    const first = await settle();
    await tabClick("tracker"); await settle();
    await js("history.back()");
    const back = await settle();
    check("back: the page before with its filters, sort and job", JSON.stringify(back) === JSON.stringify(first), diff(first, back));
    await js("history.forward()");
    const fwd = await settle();
    check("forward: the Tracker again", fwd.tab === "tracker", fwd.hash);
    writeFileSync(OUT, JSON.stringify(saved));
  } else {
    const saved = JSON.parse(readFileSync(OUT, "utf8"));
    for (const { name, hand } of saved) {
      await send("Page.navigate", { url: `${BASE}/${hand.hash}` });       // a copied link, after a server restart
      await until("document.querySelector('#who [data-person]')", 10000);
      const got = await settle();
      check(`${name}: a copied link after a server restart shows the same page`, JSON.stringify(got) === JSON.stringify(hand), diff(hand, got));
    }
    check("replay: the service worker controls the page", await js("return !!navigator.serviceWorker?.controller"), "no controller");
  }
  check("no page errors", !errors.length, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
} finally {
  console.log(JSON.stringify({ checks }));
  close();
  process.exit(0);
}

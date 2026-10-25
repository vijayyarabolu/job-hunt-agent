// GOAL9 verifier findings on the notice line above the filters (tests/test_goal9_banner.py): each person's last
// notices are drawn in the first frame, so a slow /api/health never pushes the filters and rows down after they
// painted; a profile switch shows that person's notices at once (never the previous person's); the drift dismissal
// ignores percentages ticking; after a switch the address no longer names the previous person.
// usage: node goal9_banner.mjs <base url> <token> <chrome path> <profile dir>
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR] = process.argv.slice(2);
const { send, js, until, errors, checks, check, state, close } = await start(CHROME, PROFILE_DIR);
const HELD_MS = 1500;
// SHIFTS records what the notice line pushes: the filter bar and the main area
const SHIFTS = `window.__shifts = []; new PerformanceObserver((l) => { for (const e of l.getEntries()) for (const s of e.sources)
  if (s.node?.matches?.("#filters, #bench")) window.__shifts.push(Math.round(e.startTime) + "ms " + (s.node.id || s.node.className) + " " + Math.round(s.previousRect.y) + "->" + Math.round(s.currentRect.y)); })
  .observe({ type: "layout-shift", buffered: true });`;
// the notices are in the status button (its title lists them; the popover shows them), not a line of their own
const banner = () => js("return document.querySelector('#status').title");
const holdHealth = () => { state.holdNext = { url: "/api/health", body: "", ms: HELD_MS }; };
let loads = 0;
const go = async (hash) => {                               // a new query each time: a hash-only change does not reload
  await send("Page.navigate", { url: `${BASE}/?n=${++loads}#${hash}` });
  await until("document.querySelectorAll('#list .row[data-i]').length >= 1", 10000);
};
const pickProfile = async (name) => {
  await js("document.querySelector('#who').click(); return 1");
  await until("!document.querySelector('#pop').hidden");
  await js(`[...document.querySelectorAll('#pop .opt')].find((o) => o.textContent.startsWith(${JSON.stringify(name)})).click(); return 1`);
};
try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/*" }] });
  await send("Page.addScriptToEvaluateOnNewDocument", { source: SHIFTS });
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:other` });
  await until("document.querySelectorAll('#list .row[data-i]').length >= 1", 10000);
  check("first visit: the drift notice shows", await until("document.querySelector('#status').title.includes('Data drift')", 8000), await banner());
  await go("recommended:tester");
  await until("document.querySelector('#status').title.includes('Data drift')", 8000);

  // a slow health answer: the remembered notice is there from the first frame, nothing moves when it lands
  holdHealth();
  await go("recommended:tester");
  const rowsAt = await js("return performance.now()");    // rows are on screen from here: nothing may move any more
  check("the remembered notice is drawn before /api/health answers", (await banner()).includes("Data drift") && state.holdNext === null, await banner());
  await sleep(HELD_MS + 700);
  const late = (await js("return window.__shifts")).filter((x) => parseInt(x, 10) >= rowsAt);   // "<ms>ms <node> <from>-><to>"
  check("the filters and rows keep their place when /api/health lands", late.length === 0, `${late.join(" | ")} (all: ${(await js("return window.__shifts")).join(" | ")})`);

  // dismiss: remembered by what drifts, not by the numbers
  await js("document.querySelector('#status').click(); return 1");
  await until("[...document.querySelectorAll('#pop .lnk')].some((b) => b.textContent === 'Hide until it changes')", 3000);
  await js("[...document.querySelectorAll('#pop .lnk')].find((b) => b.textContent === 'Hide until it changes').click(); document.querySelector('#pop').hidden = true; return 1");
  check("Hide until it changes hides the drift notice", !(await banner()).includes("Data drift"), await banner());
  const kept = await js("return localStorage.getItem('jh.drift.tester') || ''");
  check("the dismissal ignores the percentages", kept.includes("Data drift") && !/\d/.test(kept), kept);

  // profile switch while /api/health is slow: the other person's notices at once, never this person's
  holdHealth();
  await pickProfile("Olive");
  check("a switch shows the other person's notice at once", await until("document.querySelector('#status').title.includes('Data drift')", 400), await banner());
  // GOAL35 §1: the address carries the page's state, the person included: now the other one, never the previous
  check("the address no longer names the previous person", await js("const p = new URLSearchParams(location.hash.split('?')[1] || ''); return location.hash.startsWith('#recommended') && p.get('p') === 'other'"), await js("return location.hash"));
  await sleep(HELD_MS + 300);
  await send("Page.reload");
  await until("document.querySelectorAll('#list .row[data-i]').length >= 1", 10000);
  check("a reload stays on the person switched to", await js("return document.querySelector('#who .who-name').textContent === 'Olive Other'"),
    await js("return document.querySelector('#who .who-name').textContent + ' ' + location.hash"));
  await sleep(500);
  holdHealth();
  await pickProfile("Tess");
  check("switching back hides the dismissed notice at once", await until("!document.querySelector('#status').title.includes('Data drift')", 400), await banner());
  await sleep(HELD_MS + 300);
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
} finally {
  console.log(JSON.stringify({ checks }));
  close();
}

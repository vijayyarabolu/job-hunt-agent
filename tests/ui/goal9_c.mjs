// GOAL9 area C (the job detail pane) in headless Chrome against a throwaway DB, Apollo / Hunter mocked in the server
// (tests/test_goal9_c.py): the ⋯ menu closing rules with real pointer / touch sequences, Find contacts one click from
// the pane and the phone sheet (free signals first, the cost asked before any paid request, the tracker row only when
// a contact is saved), every pane action optimistic and under 150 ms to the first visible change, hover labels on
// every control. Screenshots go to docs/evidence/v9/c/.
// usage: node goal9_c.mjs <base url> <token> <chrome path> <profile dir> <fake resumegen url> [shots dir]
import { mkdirSync, writeFileSync } from "node:fs";
import { sleep, start } from "./cdp.mjs";
import { hoverTip, missingTips } from "./tips.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, FAKE, SHOTS = ""] = process.argv.slice(2);
const { send, js, until, key, errors, checks, check, click, clickText, server, state, close } = await start(CHROME, PROFILE_DIR);
const P = "tester";
const MORE = "#detail .acts [aria-haspopup=menu]";
const FIND = "#people .find-c";
const FIND_TOP = "#detail .acts [aria-label^='Find contacts']";   // the actions row's person button
const FAST_MS = 150;
const timings = {};
let loads = 0;

const centre = (sel, text) => js(`const els = [...document.querySelectorAll(${JSON.stringify(sel)})];
  const el = els.find((x) => (${JSON.stringify(text || "")} ? x.textContent.trim().startsWith(${JSON.stringify(text || "")}) : true) && x.getClientRects().length);
  if (!el) return null; el.scrollIntoView({ block: "nearest" }); const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }`);
async function mouseAt(x, y) {
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y });
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
  await sleep(120);
}
async function mouse(sel, text) {           // a real click at the element's centre
  const p = await centre(sel, text);
  if (p) await mouseAt(p.x, p.y);
  return !!p;
}
async function tapAt(x, y) {
  await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
  await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await sleep(150);
}
async function tap(sel, text) {
  const p = await centre(sel, text);
  if (p) await tapAt(p.x, p.y);
  return !!p;
}
async function wheel(sel, dy) {                // a point of the pane that no open menu covers
  const p = await js(`const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(), pop = document.querySelector('#pop');
    for (let y = r.bottom - 40; y > r.top; y -= 40) for (let x = r.left + 40; x < r.right; x += 80) {
      const el = document.elementFromPoint(x, y); if (el && !el.closest('#pop, button, a') && el.closest(${JSON.stringify(sel)})) return { x, y }; }
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 }`);
  await send("Input.dispatchMouseEvent", { type: "mouseWheel", x: p.x, y: p.y, deltaX: 0, deltaY: dy });
  await sleep(150);
}
async function drag(x, y0, y1) {             // a touch drag (swipe / scroll) in three moves
  await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y: y0 }] });
  for (const f of [0.3, 0.6, 1]) await send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: y0 + (y1 - y0) * f }] });
  await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await sleep(350);
}
// click -> first visible change, measured inside the page (rAF polling); -1 when nothing changed within 3 s
async function timed(name, sel, changed) {
  const ms = await js(`const el = document.querySelector(${JSON.stringify(sel)}); if (!el) return -2; el.scrollIntoView({ block: "nearest" });
    const t0 = performance.now(); el.click();
    return await new Promise((done) => { const tick = () => { if (${changed}) done(Math.round((performance.now() - t0) * 10) / 10);
      else if (performance.now() - t0 > 3000) done(-1); else requestAnimationFrame(tick); }; tick(); })`);
  timings[name] = ms;
  check(`${name}: first visible change under ${FAST_MS} ms`, ms >= 0 && ms < FAST_MS, `${ms} ms`);
  return ms;
}
const popOpen = () => js("return !document.querySelector('#pop').hidden");
const popItems = () => js("return [...document.querySelectorAll('#pop .opt')].map((o) => o.textContent.trim())");
const toasts = () => js("return [...document.querySelectorAll('.toast p')].map((p) => p.textContent)");
const text = (sel) => js(`return document.querySelector(${JSON.stringify(sel)})?.textContent || ''`);
const lookups = () => js("return (window.__req || []).filter((u) => u.includes('/api/contacts/lookup')).length");
const logFetch = () => js("if (!window.__req) { window.__req = []; const f = window.fetch; window.fetch = (u, o) => { window.__req.push(String(u)); return f(u, o); }; } return true");
const rowsOf = () => js("return document.querySelectorAll('#list .row[data-i]').length");
const trackerRows = async (q) => (await server(`/api/tracker?profile=${P}&q=${q}`)).rows;
async function shot(name) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  const r = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(`${SHOTS}/${name}.png`, Buffer.from(r.data, "base64"));
}
async function load(hash, w, h, mobile) {
  await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: mobile ? 2 : 1, mobile });
  await send("Emulation.setTouchEmulationEnabled", { enabled: mobile });
  await send("Page.navigate", { url: `${BASE}/?n=${++loads}#${hash}` });
  await sleep(300);
  await until("document.querySelectorAll('#list .row[data-i]').length >= 4 && !document.querySelector('#list[aria-busy]')", 10000);
  await until("document.getElementById('tip') && document.body.classList.contains('rg-on')", 8000);   // the lazy modules
  await logFetch();
}
const back = async () => { await send("Page.bringToFront"); await sleep(200); await js("document.dispatchEvent(new Event('visibilitychange'))"); };
async function answerBack(label) {       // "Did you apply?" after an Apply ↗ (a new tab opened)
  await back();
  if (await until("document.querySelector('dialog.back-dlg[open]')", 3000)) {
    await clickText("dialog.back-dlg .btn", label);
    await until("!document.querySelector('dialog.back-dlg[open]')");
  }
}
async function labelsOk(name, scope) {
  const missing = await missingTips(js, scope);
  check(`labels: ${name}`, missing.length === 0, JSON.stringify(missing));
}

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/*" }] });
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:${P}` });
  check("feed loads", await until("document.querySelectorAll('#list .row[data-i]').length >= 4", 10000), await rowsOf());
  await until("document.getElementById('tip') && document.body.classList.contains('rg-on')", 8000);
  await logFetch();
  const uids = (await server(`/api/jobs?profile=${P}&tab=recommended`)).jobs.map((j) => j.uid);
  await until(`document.querySelector('${MORE}')`);
  await labelsOk("a plain job's pane (desktop)", "#detail");

  // ---- the ⋯ menu, real pointer sequences ----
  await timed("⋯ opens", MORE, "!document.querySelector('#pop').hidden");
  check("⋯ lists its items", (await popItems()).join() === "Copy JD,Copy JD + keywords,Open the posting ↗,Add a job by link", await popItems());
  await labelsOk("the ⋯ menu", "#pop");
  await shot("menu-open-1440");
  await mouse("#pop .opt", "Copy JD");
  const closedOnPick = !(await popOpen());
  await until("document.querySelector('.toast p')");
  check("⋯ closes on pick (Copy JD)", closedOnPick && (await toasts()).some((t) => t.startsWith("Copied JD")), JSON.stringify(await toasts()));
  await mouse(MORE);
  await mouseAt(1000, 500);
  check("⋯ closes on an outside click", !(await popOpen()));
  await mouse(MORE);
  await key("Escape");
  check("⋯ closes on Esc", !(await popOpen()));
  await mouse(MORE);
  await wheel("#detail", 240);
  check("⋯ closes when the pane scrolls", !(await popOpen()));
  await mouse(MORE);
  await mouse("#status");                                  // the status button's popover (one person here: no person menu click)
  check("⋯ closes when another menu opens", (await popOpen()) && (await js("return document.querySelector('#pop').getAttribute('aria-label')")) === "Status");
  await key("Escape");
  await mouse(MORE);
  await mouse(MORE);
  check("a second click on ⋯ closes it", !(await popOpen()));
  await mouse(MORE);
  await mouse("#pop .opt", "Open the posting");
  await sleep(300);
  await back();
  check("⋯ closes after Open the posting ↗ (new tab, back)", !(await popOpen()) && (await js(`return document.querySelector('${MORE}').getAttribute('aria-expanded')`)) === "false");
  await mouse(MORE);
  await mouse("#pop .opt", "Add a job");
  check("⋯ closes after Add a job by link and the panel opens", !(await popOpen()) && await until("document.body.classList.contains('panel-open') && document.querySelector('#detail #add-url')"),
    await js("return document.body.className + ' ' + (document.querySelector('#detail h2')?.textContent || '')"));
  await key("Escape");                                   // Esc inside the link box only blurs it (keys.js); a second Esc closes
  await key("Escape");
  check("Esc closes the Add a job panel", await until("!document.body.classList.contains('panel-open') && document.querySelector('#detail .acts')"),
    await js("return document.body.className + ' active=' + document.activeElement?.tagName"));
  check("focus returns to the list after the panel closes", (await js("return document.activeElement?.id")) === "list",
    await js("return document.activeElement?.tagName + '#' + document.activeElement?.id"));

  // ---- Find contacts, desktop ----
  check("Find contacts is one click from the pane", await js(`return !!document.querySelector('${FIND}')`));
  await js("document.querySelector('#detail').scrollTop = 0");
  check("the actions row's Find contacts is on screen without scrolling", await js(`const b = document.querySelector("${FIND_TOP}"); if (!b) return false;
    const r = b.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth`));
  await timed("Find contacts", FIND_TOP, "document.querySelector('#people .finder-body')?.textContent.includes('Loading') || document.querySelector('#people .finder-domain')");
  check("it brings the Find contacts section into view", await until("(() => { const r = document.querySelector('#people').getBoundingClientRect(); return r.top >= 0 && r.top < innerHeight / 2; })()", 3000),
    await js("return Math.round(document.querySelector('#people').getBoundingClientRect().top)"));
  check("the free signals render", await until("document.querySelector('#people .finder-domain') && document.querySelector('#people .suggestions')", 8000), (await text("#people")).slice(0, 200));
  check("opening it sends no lookup", (await lookups()) === 0, await lookups());
  const people = await text("#people");
  check("it names the person in the JD and the domain from the JD link", people.includes("Jane Doe") && (await js("return document.querySelector('#fd-domain').value")) === "acme.ai", people.slice(0, 300));
  check("LinkedIn search links are there", (await js("return document.querySelectorAll('#people .finder-links a').length")) >= 4);
  check("Apollo and Hunter show their cost before any click", people.includes("Look up with Apollo (0 credits)") && people.includes("Look up with Hunter (up to 1 credit)"), people.slice(0, 600));
  check("the Hunter account line shows searches left", await until("document.querySelector('#people .hunter-account')?.textContent.includes('49 searches')", 5000), await text("#people .hunter-account"));
  await labelsOk("Find contacts open (desktop)", "#detail");
  await shot("find-contacts-1440");
  check("no tracker row was made by opening it", (await trackerRows("Acme")).length === 0);
  await clickText("#people .provider .btn", "Look up with Apollo");
  check("Apollo asks first with the cost", await until("document.querySelector('#ask[open]')") && (await text("#ask-h")) === "Ask Apollo? It costs 0 credits", await text("#ask"));
  check("nothing was sent before the answer", (await lookups()) === 0);
  await labelsOk("the credit ask dialog", "#ask");
  await shot("apollo-ask-1440");
  await click("#ask .btn.ghost");
  await sleep(200);
  check("Cancel sends nothing", !(await js("return document.querySelector('#ask').open")) && (await lookups()) === 0);
  await clickText("#people .provider .btn", "Look up with Apollo");
  await until("document.querySelector('#ask[open]')");
  await click("#ask-ok");
  check("OK sends exactly one lookup and shows Apollo's people", await until("document.querySelector('#people')?.textContent.includes('Sam Lee')", 8000) && (await lookups()) === 1, `${await lookups()} lookups`);
  check("the spend is reported and the next click would be free", (await toasts()).some((t) => t.startsWith("Apollo: 0 credits used")) && (await text("#people")).includes("Show Apollo results (free)"), JSON.stringify(await toasts()));
  check("still no tracker row after a lookup", (await trackerRows("Acme")).length === 0);
  await clickText("#people .provider .btn", "Look up with Hunter");
  check("Hunter asks first with the cost", await until("document.querySelector('#ask[open]')") && (await text("#ask-h")) === "Spend up to 1 Hunter credit?", await text("#ask"));
  await click("#ask-ok");
  check("Hunter's answer shows after one more lookup", await until("[...document.querySelectorAll('#people .suggestion')].some((s) => s.textContent.includes('source: Hunter'))", 8000) && (await lookups()) === 2, `${await lookups()} lookups`);
  // saving a contact creates the row, only then
  await js("[...document.querySelectorAll('#people .suggestion')].find((s) => s.textContent.includes('Jane Doe')).querySelector('.btn').click()");
  check("Add to contacts says where it went", await until("[...document.querySelectorAll('.toast p')].some((p) => p.textContent.startsWith('Added Jane Doe'))"), JSON.stringify(await toasts()));
  const made = await trackerRows("Acme");
  check("the tracker row is created as saved, not applied", made.length === 1 && made[0].status === "saved" && made[0].job_uid === uids[0] && !made[0].applied_on, JSON.stringify(made.map((r) => [r.status, r.applied_on])));
  check("the pane shows the new row without a reload", await until("document.querySelector('#detail #app-h') && document.querySelector('#people .finder-domain')", 6000), (await text("#detail .side")).slice(0, 120));
  await mouse(MORE);
  check("⋯ now offers the tracker row", (await popItems()).some((t) => t.startsWith("Tracker row")), await popItems());
  await mouse("#pop .opt", "Tracker row");
  check("⋯ closes after Tracker row and the editor opens", !(await popOpen()) && await until("document.querySelector('#detail form.editor')"));
  check("the editor's Find contacts arrives lazily", await until("document.querySelector('#detail .finder .btn')", 5000));
  await labelsOk("the tracker row editor's Find contacts section", "#detail .finder");
  await key("Escape");
  await until("!document.body.classList.contains('panel-open')");

  // ---- the rest of the pane (job 1) ----
  await js("document.querySelector('#list').focus()");
  await key("j");
  await until(`document.querySelector('#job-1[aria-selected=true]') && document.querySelector('${FIND}')`);
  await timed("Save", "#detail .acts [aria-label^='Save']", "document.querySelector(\"#detail .acts [aria-label^='Save']\")?.getAttribute('aria-pressed') === 'true'");
  check("Save marks the row at once", await until("[...document.querySelectorAll('#job-1 .tag')].some((t) => t.textContent === 'Saved')") && await until("[...document.querySelectorAll('.toast p')].some((t) => t.textContent.startsWith('Saved: Acme'))"),
    await js("return [...document.querySelectorAll('#job-1 .tag')].map((t) => t.textContent).join() + ' | ' + [...document.querySelectorAll('.toast p')].map((p) => p.textContent).join(' | ')"));
  check("the resume picker is there", await js("return !!document.querySelector('#detail .use-res select')"));
  check("the resume picker stays visible once the resume layer has loaded", await js(`const l = document.querySelector('#detail .use-res');
    const r = l.getBoundingClientRect(); return document.body.classList.contains('rg-on') && getComputedStyle(l).opacity === '1' && getComputedStyle(l).position === 'static' && r.width > 120`),
    await js("const l = document.querySelector('#detail .use-res'); return getComputedStyle(l).opacity + ' ' + getComputedStyle(l).position + ' ' + Math.round(l.getBoundingClientRect().width)"));
  await js("const s = document.querySelector('#detail .use-res select'); if (s) { s.value = s.options[s.options.length - 1].value; s.dispatchEvent(new Event('change')); }");
  const picked = await js("return document.querySelector('#detail .use-res select').value");
  state.failNext = "/api/feedback";
  await timed("Like", "#detail .acts [aria-label^='Like']", "document.querySelector(\"#detail .acts [aria-label^='Like']\")?.getAttribute('aria-pressed') === 'true'");
  check("a refused Like rolls back with a toast", await until("[...document.querySelectorAll('.toast p')].some((p) => p.textContent.startsWith('Not saved'))") && await until("document.querySelector(\"#detail .acts [aria-label^='Like']\")?.getAttribute('aria-pressed') === 'false'"), JSON.stringify(await toasts()));
  check("the resume pick survives the re-render", (await js("return document.querySelector('#detail .use-res select').value")) === picked, picked);
  await timed("Worth ?", "#detail .worth-line .info", "[...document.querySelectorAll('.toast p')].some((p) => p.textContent.startsWith('Priority, 0'))");
  await timed("Show all (JD)", "#detail .jd > .lnk", "document.querySelector('#detail .jd-text.open')");
  check("Show all relabels itself", (await text("#detail .jd > .lnk")) === "Show less");
  await click("#detail .ats-box summary");
  check("the ATS box loads on open", await until("document.querySelector('#detail .ats-box[open]') && !document.querySelector('#detail .ats-box').textContent.includes('Loading')", 8000), (await text("#detail .ats-box")).slice(0, 120));
  check("the company panel lists the other Acme roles", await until("document.querySelectorAll('#detail .roles .r-t').length >= 1"), await text("#detail .side-block"));
  const other = await text("#detail .roles .r-t");
  await click("#detail .roles .r-t");
  check("a company-panel role opens that job", await until(`document.querySelector('#detail .d-title')?.textContent === ${JSON.stringify(other)}`), await text("#detail .d-title"));
  await labelsOk("the company panel + JD open", "#detail");
  await js("document.querySelector('#list').focus()");
  await js("document.querySelector('#detail').scrollTop = 0");
  await key("b");
  check("b scrolls to Why this match", await until("Math.abs(document.querySelector('#breakdown').getBoundingClientRect().top - document.querySelector('#detail').getBoundingClientRect().top) < 40"),
    await js("return [document.querySelector('#breakdown').getBoundingClientRect().top, document.querySelector('#detail').getBoundingClientRect().top]"));
  // Mark applied (job 1): the pane shows the row at once, Undo takes it back
  await js("document.querySelector('#list').focus()");
  await key("k"); await key("j");
  await until("document.querySelector('#job-1[aria-selected=true]')");
  await timed("Mark applied", "#detail .acts .btn:not(.primary)", "document.querySelector('#apply-dlg')?.open");
  check("the Mark applied dialog is up", await until("document.querySelector('#apply-dlg')?.open", 4000),
    await js("return (document.querySelector('#apply-dlg')?.open ? 'open' : 'closed') + ' | ' + [...document.querySelectorAll('.toast p')].map((p) => p.textContent).join(' | ') + ' | ' + document.querySelector('#detail .acts')?.textContent"));
  await labelsOk("the Mark applied dialog", "#apply-dlg");
  await click("#ad-without");
  check("Mark applied marks the row and the pane at once", await until("[...document.querySelectorAll('#job-1 .tag')].some((t) => t.textContent === 'Applied') && document.querySelector('#detail .acts .btn.done')", 6000));
  check("the pane's Your application block appears without a reload", await until("document.querySelector('#detail #app-h')", 6000), (await text("#detail .side")).slice(0, 120));
  await labelsOk("an applied job's pane with its row", "#detail");
  await shot("applied-1440");
  await js("[...document.querySelectorAll('.toast .undo')].at(-1)?.click()");
  check("Undo puts the pane back", await until("!document.querySelector('#detail .acts .btn.done') && ![...document.querySelectorAll('#job-1 .tag')].some((t) => t.textContent === 'Applied') && !/Status\\s*applied/.test(document.querySelector('#detail .side')?.textContent || '')", 6000),
    (await text("#detail .side")).slice(0, 120));   // the row Mark applied made stays, as saved (trackapi.undo reverts the status only)
  // Generate resume (goal8 covers the rest)
  await clickText("#detail .acts .btn", "Generate resume");
  check("Generate resume queues and relabels", await until("document.querySelector('#job-1 .rgc')?.textContent === 'Resume queued' && [...document.querySelectorAll('#detail .acts .btn')].some((b) => b.textContent === 'Generate again')", 6000));
  // Apply ↗ remembers the pending apply
  await js("document.querySelector('#list').focus()");
  await key("j");
  await until("document.querySelector('#job-2[aria-selected=true]')");
  await click("#detail .acts .btn.primary");
  await sleep(400);
  check("Apply ↗ remembers the pending apply", await until(`(await (await fetch('/api/pending?profile=${P}&peek=1')).json()).ask.length === 1`, 5000));
  check("Apply ↗ shows its saved row at once (row tag, no reload)", await until("[...document.querySelectorAll('#job-2 .tag')].some((t) => t.textContent === 'Saved')", 4000),
    await js("return [...document.querySelectorAll('#job-2 .tag')].map((t) => t.textContent).join()"));
  await until("document.querySelector('#detail #app-h')", 4000);
  await click(MORE);
  check("⋯ offers the tracker row right after Apply ↗", (await popItems()).some((t) => t.startsWith("Tracker row")), await popItems());
  await key("Escape");
  await answerBack("No");
  // Not interested: the row leaves at once, the toast carries reason chips, Undo brings it back
  const before = await rowsOf();
  await timed("Not interested", "#detail .acts [aria-label^='Not interested']", "document.querySelector('#job-2')?.classList.contains('leave') || document.querySelectorAll('#list .row[data-i]').length < " + before);
  check("Not interested drops the row and asks why", await until(`document.querySelectorAll('#list .row[data-i]').length === ${before - 1}`) && await until("document.querySelectorAll('.toast .why .chip').length === 5"), await rowsOf());
  await labelsOk("the Not interested toast", ".toast");
  await click(".toast .why .chip");
  check("a reason chip records the reason", await until("document.querySelector('.toast .why')?.textContent.startsWith('Reason:')"), await text(".toast .why"));
  state.holdNext = { url: "/api/triage", body: '"restored"', ms: 800 };   // the Undo's restore is slow to land...
  await js("[...document.querySelectorAll('.toast .undo')].at(-1)?.click()");
  check("Undo brings the row back", await until(`document.querySelectorAll('#list .row[data-i]').length === ${before}`), await rowsOf());
  for (let t = 0; t < 4000 && state.holdNext; t += 50) await sleep(50);   // the restore is on the wire (and held)
  check("the Undo's restore was sent and held", state.holdNext === null);
  // ...and x comes at once, while it is still in flight: the dismiss must reach the server after it, not before (the
  // restore landing last put the job back in the feed: a race seen in 2 of ~10 full runs)
  // Restore on Archived
  await js("document.querySelector('#list').focus()");
  await key("x");
  check("x dismisses the selected job", await until(`document.querySelectorAll('#list .row[data-i]').length === ${before - 1}`), await rowsOf());
  await sleep(1000);                                       // past the held restore: the last write the server saw wins
  check("the dismiss reaches the server", await until(`(await (await fetch('/api/jobs?profile=${P}&tab=archived')).json()).jobs.length === 1`, 5000),
    await js(`const a = await (await fetch('/api/jobs?profile=${P}&tab=archived')).json(); const r = await (await fetch('/api/jobs?profile=${P}&tab=recommended')).json();
      return JSON.stringify({ archived: a.jobs.length, total: a.total, rec: r.jobs.map((j) => [j.title, j.triage]), req: (window.__req || []).filter((u) => u.includes('triage') || u.includes('feedback')) })`));
  await key("3");                                          // the side menu's order (Recommended, Saved, Archived, …)
  check("Archived lists the dismissed job", await until("document.body.dataset.view === 'jobs' && document.querySelectorAll('#list .row[data-i]').length >= 1 && document.querySelector('#detail .acts [aria-label^=\"Restore\"]')", 8000), await rowsOf());
  await labelsOk("an archived job's pane", "#detail");
  await timed("Restore", "#detail .acts [aria-label^='Restore']", "document.querySelector('#job-0')?.classList.contains('leave') || document.querySelectorAll('#list .row[data-i]').length === 0");
  check("Restore drops it from Archived with a toast", await until("[...document.querySelectorAll('.toast p')].some((p) => p.textContent.startsWith('Restored to the feed'))"));
  await key("1");
  await until("document.querySelectorAll('#list .row[data-i]').length >= 4 && document.querySelector('#detail .acts')");

  // ---- hover labels: the shared tip on the icons, by keyboard focus too ----
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 700, y: 60 });
  for (const [name, sel] of [["heart", "#detail .acts [aria-label^='Like']"], ["bookmark", "#detail .acts [aria-label^='Save']"],
    ["not interested", "#detail .acts [aria-label^='Not interested']"], ["⋯", MORE], ["Find contacts", FIND]]) {
    await js(`document.querySelector(${JSON.stringify(sel)}).scrollIntoView({ block: "center" })`);
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 700, y: 60 });
    await sleep(100);
    const tip = await hoverTip(send, js, sleep, sel);
    check(`hover tip on ${name}`, tip.found && tip.text.length > 3 && !tip.overlaps, JSON.stringify(tip));
  }
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 700, y: 60 });
  await sleep(200);
  await js("document.querySelector('#detail').scrollTop = 0; return 1");   // a taller side column (answers) left the
  await sleep(100);                                   // actions row scrolled away; focusing it would scroll, which hides a tip
  await js("document.querySelector('#list').focus()");
  await key("Tab");                                   // keyboard mode, then focus lands on a labelled control
  await js("document.querySelector(\"#detail .acts [aria-label^='Save']\").focus()");
  await sleep(450);
  check("keyboard focus shows the tip", (await text("#tip")).startsWith("Save for later") && !(await js("return document.getElementById('tip').hidden")), await text("#tip"));
  // the resume panel in the pane
  const st = await (await fetch(`${FAKE}/__state`)).json();
  const rg = Object.values(st.jobs).find((j) => j.source_job_id === uids[1]);
  await fetch(`${FAKE}/__set`, { method: "POST", body: JSON.stringify({ id: rg.id, status: "ready", attempts: 1, elapsed_s: 60, drive_file_id: "F1", drive_link: "https://drive.example/F1",
    drive_name: "Tess_Tester_ML_Resume(Acme).pdf", result: { base: { id: "ml", label: "ML Engineer" }, ats: { base_pct: 60, tailored_pct: 74, label: "estimate", missing: ["gRPC"] },
      files: { pdf: "/x.pdf", png: "/x.png" }, approved: null, diff: { changed_bullets: 1, total_bullets: 5, summary: { changed: true, ops: [["replace", "a", "b"]] } } } }) });
  await until("document.querySelector('#job-1 .rgc')?.textContent === 'Resume ready'", 9000);
  await js("document.querySelector('#job-1 .rgc').click()");
  check("the resume panel opens in the pane", await until("[...document.querySelectorAll('#detail h2')].some((x) => x.textContent === 'Tailored resume')"));
  await labelsOk("the resume panel in the pane", "#detail");
  await key("Escape");

  // ---- the phone sheet ----
  await load(`recommended:${P}`, 390, 844, true);
  await tap("#job-0 .row-title");                         // the title: the row's centre can be its resume chip
  check("phone: a tap opens the sheet", await until("document.body.classList.contains('sheet-open')"));
  await until(`document.querySelector('${MORE}')`);
  await sleep(400);                                        // the sheet's slide-up transition
  await labelsOk("the phone sheet", "#detail");
  await tap(MORE);
  check("phone: ⋯ opens on a tap", await popOpen(), await popItems());
  await shot("menu-open-phone");
  await tap("#pop .opt", "Copy JD");
  check("phone: ⋯ closes on pick", !(await popOpen()) && await until("[...document.querySelectorAll('.toast p')].some((p) => p.textContent.startsWith('Copied JD'))"));
  await tap(MORE);
  await tapAt(200, 300);
  check("phone: ⋯ closes on an outside tap", !(await popOpen()));
  await tap(MORE);
  await drag(60, 560, 380);                                // a touch scroll outside the popover
  check("phone: ⋯ closes when the sheet scrolls", !(await popOpen()));
  check("phone: the sheet stays open", await js("return document.body.classList.contains('sheet-open')"));
  check("phone: Find contacts is one tap away in the sheet", await js(`const b = document.querySelector('${FIND}'); if (!b) return false; b.scrollIntoView({ block: 'center' }); const r = b.getBoundingClientRect(); return r.height >= 44`));
  await sleep(200);
  await shot("find-contacts-phone-closed");
  await timed("phone: Find contacts", FIND, "document.querySelector('#people .finder-body')?.textContent.includes('Loading')");
  check("phone: the free signals render", await until("document.querySelector('#people .finder-domain')", 8000));
  check("phone: opening it sends no lookup", (await lookups()) === 0);
  await labelsOk("Find contacts open (phone)", "#detail");
  await shot("find-contacts-phone");
  await js("const d = document.querySelector('#fd-domain'); d.value = 'acme-two.io'");
  await clickText("#people .finder-domain .btn", "Use");
  await until("document.querySelector('#people')?.textContent.includes('Look up with Apollo (0 credits)')", 6000);
  await js("[...document.querySelectorAll('#people .provider .btn')].find((b) => b.textContent.startsWith('Look up with Apollo')).click()");
  check("phone: Apollo asks first with the cost", await until("document.querySelector('#ask[open]')") && (await text("#ask-h")) === "Ask Apollo? It costs 0 credits", await text("#ask"));
  await shot("apollo-ask-phone");
  await click("#ask .btn.ghost");
  check("phone: Cancel sends nothing", (await lookups()) === 0);
  await js("document.querySelector('#people .find-c').blur()");
  const p = await centre(FIND);
  await sleep(300);                                        // centre() scrolled: a scroll event would cancel the press
  await js("window.__ev = []; for (const t of ['pointerdown', 'touchstart', 'touchend', 'scroll', 'focusin', 'focusout']) document.addEventListener(t, (e) => window.__ev.push(t + ':' + (e.target.className || e.target.tagName || '')), { capture: true, passive: true })");
  await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: p.x, y: p.y }] });
  const seen = [];
  for (let t = 0; t < 800; t += 100) { await sleep(100); seen.push(await js("const t = document.getElementById('tip'); return t.hidden ? '-' : t.textContent.slice(0, 13)")); }
  const pressed = seen.at(-1);
  await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await sleep(150);
  check("phone: a long press shows the tip and release hides it", pressed.startsWith("Find contacts") && await js("return document.getElementById('tip').hidden"),
    `${seen.join(" ")} | ${await js("return window.__ev.join(' ')")}`);
  await sleep(800);                                        // the tap also re-opened the finder: let it settle
  await key("Escape");
  check("phone: Esc closes the sheet and focus returns to the list", await until("!document.body.classList.contains('sheet-open')") && (await js("return document.activeElement?.id")) === "list",
    await js("return document.body.className + ' active=' + document.activeElement?.tagName + '#' + document.activeElement?.id"));
  await tap("#job-1 .row-title");                         // the title: the row's centre can be its resume chip, which opens the resume panel
  await until("document.body.classList.contains('sheet-open') && document.querySelector('#detail .acts-icons')");
  await sleep(400);                                        // the sheet has finished sliding in and its job has loaded
  await js("document.querySelector('#detail').scrollTop = 0");
  await drag(200, 160, 460);
  check("phone: a swipe down closes the sheet", await until("!document.body.classList.contains('sheet-open')"),
    await js("return document.body.className + ' | ' + document.querySelector('#detail').textContent.slice(0, 80)"));
  check("no page errors", errors.length === 0, errors.join(" | ").replace(/\s+/g, " "));
  check("timings", true, JSON.stringify(timings));
} catch (err) {
  check("runner", false, err.stack || err);
} finally {
  console.log(JSON.stringify({ checks }));
  close();
}

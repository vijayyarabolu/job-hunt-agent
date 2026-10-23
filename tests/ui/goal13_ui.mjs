// GOAL13 UI checks in headless Chrome against a throwaway DB and a fake resumegen (tests/test_goal13_ui.py): the
// Resumes tab (Queue / Ready / Done today), Apply -> resume in hand -> "Applied?" on the row -> Yes -> Done today,
// keys, remembered place, one card per req, answers, Settings, the second person, the phone.
// usage: node goal13_ui.mjs <base url> <token> <chrome path> <profile dir> <fake resumegen url>
import { sleep, start } from "./cdp.mjs";
import { settingsPage } from "./settings_page.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, FAKE] = process.argv.slice(2);
const { send, js, until, key, errors, checks, check, clickText, server, close } = await start(CHROME, PROFILE_DIR);
const settings = settingsPage({ js, until, clickText });
const fakeState = async () => (await fetch(`${FAKE}/__state`)).json();
const S = JSON.stringify;
const titles = () => js("return [...document.querySelectorAll('#list .row[data-i]')].map((r) => r.querySelector('.row-title').textContent)");
const selected = () => js("return document.querySelector('#list .row[aria-selected=true] .row-title')?.textContent || ''");
const rowOf = (t) => `[...document.querySelectorAll('#list .row[data-i]')].find((r) => r.querySelector('.row-title').textContent === ${S(t)})`;
const lineOf = (t) => js(`return ${rowOf(t)}?.querySelector('.rq-line')?.textContent || ''`);
const rowBtn = (t, label, where = ".rq-line .mini") => js(`const b = [...(${rowOf(t)}?.querySelectorAll(${S(where)}) || [])].find((x) => x.textContent === ${S(label)}); b?.click(); return !!b`);
const toasts = () => js("return [...document.querySelectorAll('.toast')].map((t) => t.textContent).join(' | ')");
const idle = () => until("!document.querySelector('#list[aria-busy]')", 8000);
const view = async (v) => {
  await js(`[...document.querySelectorAll('.rq-seg button')].find((b) => b.textContent.startsWith(${S(v)})).click()`);
  await until(`document.querySelector('.rq-seg [aria-pressed=true]')?.textContent.startsWith(${S(v)})`);
  await idle();
  await sleep(300);
};
const tabTo = async (tab, where = "#tabs") => { await js(`document.querySelector('${where} [data-tab=${tab}]').click()`); await sleep(400); await idle(); await sleep(300); };
// Apply ↗ opens the posting (recorded, no new tab), a download link is recorded, the clipboard records
const hooks = () => js(`window.__dl = []; window.__copied = []; window.__opened = [];
  const click = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () { if (this.hasAttribute('download')) window.__dl.push(this.href); else click.call(this); };
  window.open = (u) => { window.__opened.push(u); return null; };
  if (navigator.clipboard) navigator.clipboard.writeText = async (t) => { window.__copied.push(t); };
  return true`);
const back = () => js("document.dispatchEvent(new Event('visibilitychange')); return true");
const select = async (t) => { for (let i = 0; i < 30 && (await selected()) !== t; i++) await key("j"); return (await selected()) === t; };
const place = () => js("const r = document.querySelector('#list .row[aria-selected=true]'); return { sel: r?.querySelector('.row-title')?.textContent, top: document.querySelector('#list-wrap').scrollTop, h: r?.offsetHeight, y: Math.round(r?.getBoundingClientRect().top), wh: document.querySelector('#list-wrap').clientHeight }");
const samePlace = (a, b) => a.sel === b.sel && Math.abs(a.top - b.top) <= 2 && a.top > 0;
const acted = async (name) => { for (let i = 0; i < 30; i++) { if ((await fakeState()).actions.some(([, a]) => a === name)) return true; await sleep(100); } return false; };
let loads = 0;
const load = async (hash) => {
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}&n=${++loads}#${hash}` });
  await until("document.body.classList.contains('rg-on')", 8000);
  await idle();
  await hooks();
};

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await load("recommended:tester");

  // ---- §2f one card per req ----
  const recs = await js("return [...document.querySelectorAll('#list .row')].filter((r) => r.querySelector('.row-title').textContent === 'Recommendation ML Engineer').map((r) => r.textContent)");
  check("a req in two places is one card with +1 location", recs.length === 1 && recs[0].includes("+1 location"), S(recs));

  // ---- §1 the tab, Ready ----
  await until("document.querySelector('#tabs [data-tab=resumes] .n')?.textContent === '3'", 8000);
  check("the Resumes tab shows the ready count", (await js("return document.querySelector('#tabs [data-tab=resumes] .n').textContent")) === "3");
  const painted = "document.querySelectorAll('#list .row[data-i] .rq-line').length >= 3 && !document.querySelector('#list .row.sk')";
  // skeleton rows seen while the Resumes tab paints (a MutationObserver: the tab switch is async)
  const openWatched = () => js(`window.__sk = 0; const list = document.querySelector('#list');
    window.__obs?.disconnect(); window.__obs = new MutationObserver(() => { if (list.querySelector('.row.sk')) window.__sk++; });
    window.__obs.observe(list, { childList: true }); document.querySelector('#tabs [data-tab=resumes]').click(); return true`);
  let t0 = Date.now();
  await openWatched();
  await until(painted, 4000);
  const firstPaint = Date.now() - t0;
  const sk0 = await js("return window.__sk");
  check("GOAL14 §3b: the Resumes tab paints its rows in < 500 ms", firstPaint < 500 && sk0 > 0, `${firstPaint} ms, ${sk0} skeleton paints (nothing kept yet: the watch sees them)`);
  await idle();
  await tabTo("recommended");
  t0 = Date.now();
  await openWatched();
  await until(painted, 4000);
  const again = Date.now() - t0;
  const sk = await js("return window.__sk");
  check("GOAL14 §3b: back on Resumes the last board paints at once, no skeleton rows", sk === 0 && again < 500, `${sk} skeleton paints, ${again} ms`);
  await idle();
  await load("recommended:tester");
  t0 = Date.now();
  await openWatched();
  await until(painted, 4000);
  const reloaded = Date.now() - t0;
  const sk2 = await js("return window.__sk");
  check("GOAL14 §3b: after a page reload the kept board paints at once", sk2 === 0 && reloaded < 500, `${sk2} skeleton paints, ${reloaded} ms`);
  await idle();
  await sleep(300);
  const ready = await titles();
  check("Ready lists needs-you first, then newest ready, closed last",
    S(ready) === S(["AI Engineer", "Machine Learning Engineer", "Senior Machine Learning Engineer"]), S(ready));
  check("a closed posting is badged Posting closed", (await lineOf("Senior Machine Learning Engineer")).includes("Posting closed"));
  check("the Ready segment says how many need you (GOAL21 W13)", await js("return /^Ready \\d+ \\d+ needs? you$/.test(document.querySelector('.rq-seg [aria-pressed=true]')?.textContent || '')"),
    await js("return document.querySelector('.rq-seg [aria-pressed=true]')?.textContent"));
  check("a needs-you row is badged Needs you", (await lineOf("AI Engineer")).replace(/^\d+ · \d+:\d\d [AP]M/, "").startsWith("Needs you"), await lineOf("AI Engineer")); // after "n · time"
  // GOAL17 (user): an empty Queue showed "Nothing here with these filters." and a stray "null"; the tab has no filters
  const under = () => js("return document.querySelector('#list-more').textContent.trim()");
  check("GOAL17: no stray 'null' under the list", !(await under()).includes("null"), S(await under()));
  await js("[...document.querySelectorAll('.rq-seg button')].find((b) => b.textContent.startsWith('Done today')).click(); return true");
  await until("document.querySelector('.rq-seg [aria-pressed=true]')?.textContent.startsWith('Done today') && !document.querySelector('#list .row[data-i]')", 8000);
  await idle();
  check("GOAL17: an empty Done today says so, with no filters and no 'null'", (await under()) === "No applies with a tailored resume yet today.", S(await under()));   // GOAL30-app §0 words
  await js("[...document.querySelectorAll('.rq-seg button')].find((b) => b.textContent.startsWith('Ready')).click(); return true");
  await until("document.querySelectorAll('#list .row[data-i]').length === 3", 8000);
  await idle();

  // ---- keys ----
  await js("document.querySelector('#list').focus()");
  await key("j");
  const afterJ = await selected();
  await key("k");
  check("j and k move in the Resumes tab", afterJ === "Machine Learning Engineer" && (await selected()) === "AI Engineer", afterJ);
  await key("Enter");
  check("Enter opens the job", await until("document.querySelector('#detail .d-title')?.textContent === 'AI Engineer'"));
  await js("document.querySelector('#list').focus()");
  await key("m");
  check("m opens Mark applied", await until("document.querySelector('#apply-dlg[open]')"));
  await js("document.querySelector('#apply-dlg').close(); return true");

  // ---- §2g answers ----
  await js("document.querySelector('#list').focus()");
  await key("j");
  check("the answer chips are in the detail", await until("document.querySelector('#detail .ans .chip')", 6000));
  // GOAL21 W10: the job's posted pay is its own chip; "My salary ask" is the saved answer (it was one "Salary" chip)
  const salary = await js("return [...document.querySelectorAll('#detail .ans .chip')].find((c) => c.textContent === 'Posted pay')?.title || ''");
  check("the job's pay range is the Posted pay chip", salary.includes("$150,000 to $180,000"), salary);
  check("My salary ask is the saved answer, not the job's pay", await js("return [...document.querySelectorAll('#detail .ans .chip')].some((c) => c.textContent.startsWith('My salary ask'))"));
  await clickText("#detail .ans .chip", "Years of experience");
  check("a chip copies its answer", await until("window.__copied.includes('4 years')"), await js("return window.__copied"));
  await clickText("#detail .ans .chip", "LinkedIn +");
  // GOAL20 (user 23:27): an empty answer opens its editor right in the panel (Settings had the answers out of sight)
  check("an empty answer opens its editor in the panel", await until("document.activeElement?.name === 'linkedin' && document.activeElement.closest('#detail .ans')", 6000));
  await key("Escape");

  // ---- Settings: goal, an answer (a page; each field saves on its own) ----
  await settings.set("goal", "Applications a day", "5");
  await settings.set("answers", "Earliest start", "2 weeks");
  await settings.close();
  check("Settings saves the daily goal", await until("document.querySelector('.prog')?.textContent.includes('goal 5')", 8000),
    await js("return document.querySelector('.prog')?.textContent"));
  check("Settings saves an answer", (await server("/api/settings?profile=tester")).answers.start === "2 weeks");

  // ---- §2b Apply = resume in hand, §2a Applied? Yes ----
  await js("document.querySelector('#list').focus()");
  await select("Machine Learning Engineer");
  await rowBtn("Machine Learning Engineer", "Apply ↗");
  const dl = await js("return window.__dl");
  check("Apply downloads the ready resume", dl.length === 1 && dl[0].includes("/api/resumes/file?") && dl[0].includes("which=pdf"), S(dl));
  const file = await js(`const r = await fetch(${S(dl[0] || "/x")}); return [r.status, r.headers.get('content-type'), r.headers.get('content-disposition')]`);
  check("the download is the PDF under the base name", file[0] === 200 && file[1] === "application/pdf" && String(file[2]).includes("Tess_ML.pdf"), S(file));
  check("Apply copies company, title and req id", await until("window.__copied.includes('Acme · Machine Learning Engineer · Req R-100')"), await js("return window.__copied"));
  await back();
  check("coming back asks Applied? on the job's row", await until(`${rowOf("Machine Learning Engineer")}?.querySelector('.ask-in')`, 6000));
  check("and at the top of its detail", await until("document.querySelector('#detail .d-head .ask-in')"));
  await rowBtn("Machine Learning Engineer", "Yes", ".ask-in .mini");
  await until("document.querySelector('.toast')?.textContent.includes('Applied: Acme')", 8000);
  await idle();
  await sleep(600);
  const done = (await server("/api/jobs?profile=tester&tab=resumes&rq=done")).jobs;
  check("it moves to Done today", done.map((j) => j.title).includes("Machine Learning Engineer"), S(done.map((j) => j.title)));
  check("Yes marks it applied", done[0]?.app?.status === "applied", S(done[0]?.app));
  check("Yes approves its resume to Drive", await acted("approve"), S((await fakeState()).actions));
  check("the next Ready job is selected", (await selected()) === "Senior Machine Learning Engineer", await selected());
  check("the day's progress counts it", await until("document.querySelector('.prog')?.textContent.startsWith('Applied today 1')", 6000),
    await js("return document.querySelector('.prog')?.textContent"));

  // ---- Done today: Undo ----
  await view("Done today");
  await rowBtn("Machine Learning Engineer", "Undo");
  await until("document.querySelector('.toast')?.textContent.includes('Back in Ready')", 6000);
  await view("Ready");
  check("Undo puts it back in Ready", (await titles()).includes("Machine Learning Engineer"), S(await titles()));

  // ---- Not interested from the inline question ----
  await js("document.querySelector('#list').focus()");
  await select("AI Engineer");
  const opened = await js("return window.__opened.length");
  await key("a");
  check("a applies from the Resumes tab", (await js("return window.__opened.length")) === opened + 1, await js("return window.__opened"));
  await back();
  await until(`${rowOf("AI Engineer")}?.querySelector('.ask-in')`, 6000);
  await rowBtn("AI Engineer", "Not interested", ".ask-in .mini");
  check("Not interested takes it out of Ready", await until(`!${rowOf("AI Engineer")}`, 6000), S(await titles()));

  // ---- x on the Resumes tab, with the feed's Undo ----
  await js("document.querySelector('#list').focus()");
  await select("Senior Machine Learning Engineer");
  await key("x");
  const gone = await until(`!${rowOf("Senior Machine Learning Engineer")}`, 6000);
  await js("[...document.querySelectorAll('.toast .undo')].filter((b) => b.textContent === 'Undo').pop()?.click(); return true");
  check("x passes on a Ready job and Undo brings it back", gone && await until(`!!${rowOf("Senior Machine Learning Engineer")}`, 8000), S(await titles()));

  // ---- Queue: order, Generate, View queue, Move to top, Cancel ----
  await view("Queue");
  check("Queue shows generating now first", (await titles())[0] === "ML Platform Engineer" && (await lineOf("ML Platform Engineer")).includes("Generating now"), S(await titles()));
  check("the running time ticks with the list's clocks", await js(`return !!${rowOf("ML Platform Engineer")}?.querySelector('.rq-line time[data-ts][data-exact="1"]')`));
  await tabTo("recommended");
  await js("document.querySelector('#list').focus()");
  await select("Vision Machine Learning Engineer");
  await key("g");
  check("Generate says Added to queue (#n)", await until("[...document.querySelectorAll('.toast p')].some((p) => p.textContent === 'Added to queue (#2)')", 6000), await toasts());
  await clickText(".toast .undo", "View queue");
  check("View queue opens the queue", await until("document.querySelector('.rq-seg [aria-pressed=true]')?.textContent.startsWith('Queue') && [...document.querySelectorAll('#list .row-title')].some((t) => t.textContent === 'Vision Machine Learning Engineer')", 8000), S(await titles()));
  check("Queue numbers the order they will run", (await lineOf("Applied AI Engineer")).includes("#1 in queue") && (await lineOf("Vision Machine Learning Engineer")).includes("#2 in queue"),
    `${await lineOf("Applied AI Engineer")} / ${await lineOf("Vision Machine Learning Engineer")}`);
  await rowBtn("Vision Machine Learning Engineer", "Move to top");
  check("Move to top reaches the resume app", await acted("top"), S((await fakeState()).actions));
  await rowBtn("Vision Machine Learning Engineer", "Cancel");
  check("Cancel reaches the resume app", await acted("cancel"), S((await fakeState()).actions));

  // ---- §2e remember my place ----
  await tabTo("recommended");
  await js("document.querySelector('#list').focus()");
  for (let i = 0; i < 18; i++) await key("j");
  await sleep(300);
  const here = await place();
  await tabTo("tracker");
  await tabTo("recommended");
  await until(`document.querySelector('#list .row[aria-selected=true] .row-title')?.textContent === ${S(here.sel)}`, 6000);
  const there = await place();
  check("switching tabs and back keeps the list's place", samePlace(here, there), S([here, there]));
  await js("document.querySelector('#list').focus()");
  await key("a");                                         // Apply, come back: the list redraws with "Applied?" on the row
  await back();
  await until(`${rowOf(here.sel)}?.querySelector('.ask-in')`, 6000);
  await sleep(300);
  const redrawn = await place();
  // the row grew by the "Applied?" line: the list may scroll that much to keep it in view, never back to the top
  check("a refresh keeps the selected job", redrawn.sel === here.sel && Math.abs(redrawn.top - here.top) <= Math.max(60, redrawn.h - here.h + 2) && redrawn.top > 0, S([here, redrawn]));
  await rowBtn(here.sel, "Not yet", ".ask-in .mini");
  await idle();
  await sleep(300);
  await js("document.querySelector('#list').focus()");
  await key("g");                                          // a chip to open the resume panel from
  await until(`${rowOf(here.sel)}?.querySelector('.rgc')?.textContent === 'Resume queued'`, 6000);
  await sleep(500);
  const beforePanel = await place();
  await js(`${rowOf(here.sel)}.querySelector('.rgc').click(); return true`);
  await until("document.body.classList.contains('panel-open')", 6000);
  await key("Escape");
  await until("!document.body.classList.contains('panel-open')");
  await sleep(300);
  const closed = await place();
  check("closing the resume panel keeps the list's place", samePlace(beforePanel, closed), S([beforePanel, closed]));

  // ---- Apply in hand off ----
  await settings.set("answers", "Apply also downloads", false);
  await settings.close();
  await tabTo("resumes");
  await view("Ready");
  await js("window.__dl = []; window.__opened = []; return true");
  await rowBtn("Machine Learning Engineer", "Apply ↗");
  await sleep(400);
  check("Apply in hand can be turned off", (await js("return window.__dl.length")) === 0 && (await js("return window.__opened.length")) === 1,
    await js("return [window.__dl, window.__opened]"));
  await back();
  await until(`${rowOf("Machine Learning Engineer")}?.querySelector('.ask-in')`, 6000);
  await rowBtn("Machine Learning Engineer", "Not yet", ".ask-in .mini");
  await idle();

  // ---- the second person ----
  await load("recommended:tester2");
  await until("document.querySelector('#tabs [data-tab=resumes] .n')?.textContent === '1'", 8000);
  await tabTo("resumes");
  const hers = await titles();
  check("the second person sees only her own resumes", S(hers) === S(["Machine Learning Engineer"]), S(hers));
  await rowBtn("Machine Learning Engineer", "Apply ↗");
  await back();
  await until(`${rowOf("Machine Learning Engineer")}?.querySelector('.ask-in')`, 6000);
  await rowBtn("Machine Learning Engineer", "Yes", ".ask-in .mini");
  await until("document.querySelector('.toast')?.textContent.includes('Applied: Acme')", 8000);
  const herDone = (await server("/api/jobs?profile=tester2&tab=resumes&rq=done")).jobs;
  const hisDone = (await server("/api/jobs?profile=tester&tab=resumes&rq=done")).jobs;
  check("her Apply asks Applied? and Yes applies it", herDone.length === 1 && herDone[0].app?.status === "applied" && hisDone.length === 0,
    S([herDone.map((j) => j.title), hisDone.map((j) => j.title)]));

  // ---- the phone ----
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await load("recommended:tester");
  check("the phone bar has Resumes with its count", await until("document.querySelector('#btabs [data-tab=resumes] .n')?.textContent === '2'", 8000),
    await js("return document.querySelector('#btabs [data-tab=resumes]')?.textContent"));
  await tabTo("resumes", "#btabs");
  const btns = await js(`return [...(${rowOf("Machine Learning Engineer")}?.querySelectorAll('.rq-line .mini') || [])].filter((b) => b.offsetParent && b.getBoundingClientRect().height >= 44).map((b) => b.textContent)`);
  check("the phone row has its buttons", ["Apply ↗", "Mark applied", "Review resume", "Not interested"].every((b) => btns.includes(b)), S(btns));
  await rowBtn("Machine Learning Engineer", "Apply ↗");
  await js(`${rowOf("Machine Learning Engineer")}.querySelector('.row-title').click(); return true`);
  await until("document.body.classList.contains('sheet-open')", 6000);
  await back();
  check("the phone sheet asks Applied? at its top", await until("document.querySelector('#detail .d-head .ask-in')", 6000));
  await clickText("#detail .d-head .ask-in .mini", "Not yet");
  await sleep(300);
  await key("Escape");
  await tabTo("recommended", "#btabs");
  await js("document.querySelector('#list-wrap').scrollTop = 900; return true");
  await sleep(300);
  const t = await js("const w = document.querySelector('#list-wrap'); const r = [...document.querySelectorAll('#list .row[data-i]')].find((x) => x.offsetTop > w.scrollTop + 60); r.querySelector('.row-title').click(); return r.querySelector('.row-title').textContent");
  await until("document.body.classList.contains('sheet-open')", 6000);
  const before = { sel: t, top: await js("return document.querySelector('#list-wrap').scrollTop") };
  await key("Escape");
  await sleep(400);
  const after = await place();
  check("the phone sheet closes back to the same place", !(await js("return document.body.classList.contains('sheet-open')")) && samePlace(before, after), S([before, after]));

  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
} finally {
  console.log(JSON.stringify({ checks }));
  close();
}

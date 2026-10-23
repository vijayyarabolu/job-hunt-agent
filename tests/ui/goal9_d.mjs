// GOAL9 area D in headless Chrome (tests/test_goal9_d.py): the Saved / Applied / Interviewing / Archived / Tracker /
// Follow-ups / Stats tabs and the Tracker editing. Every change paints in place with no reload (Move to…, inline edits,
// the row editor, Mark applied), rolls back on a refused request, keeps the focus; Show columns is a live multi-choice
// menu; phone cards at 390 px; hover labels (§6) on every control of every view and state.
// usage: node goal9_d.mjs <base url> <token> <chrome path> <profile dir>  -> prints {"checks": [{name, ok, detail}]}
import { sleep, start } from "./cdp.mjs";
import { hoverTip, missingTips } from "./tips.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR] = process.argv.slice(2);
const { send, js, until, key, errors, checks, state, check: record, click, clickText, server, close } = await start(CHROME, PROFILE_DIR);
// each check is echoed on stderr as it runs, so a stuck step shows where it stopped
const check = (name, ok, detail = "") => { record(name, ok, detail); process.stderr.write(`${ok ? "ok  " : "FAIL"} ${name} ${ok ? "" : detail}\n`); };
const P = "alex";
const FAST_MS = 150;                 // click -> visible change
const JOB_ROWS = "#list, #list *";   // the job list rows are area B's: their labels are checked by B's test
const row = (app) => `.g tr[data-app="${app}"]`;
const G_ROWS = ".g tbody tr[data-app]";
// the tracker is a Sheets-like grid (tests/ui/goal19_ui.mjs checks every key); these steps drive it like a person
const colOf = (label) => js(`return [...document.querySelectorAll('.g thead th[data-j]')].findIndex((th) => th.querySelector('.g-hl').firstChild.textContent === ${JSON.stringify(label)})`);
const cell = (app, j) => `${row(app)} > :nth-child(${j + 2})`;
const cellText = (app, j) => js(`return document.querySelector(${JSON.stringify(cell(app, j))})?.textContent ?? null`);
async function pressCell(app, j, button = "left") {
  const p = await js(`const td = document.querySelector(${JSON.stringify(cell(app, j))}); td.scrollIntoView({ block: 'nearest', inline: 'center' }); await new Promise((r) => setTimeout(r, 120));
    const r = td.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }`);
  for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) await send("Input.dispatchMouseEvent", { type, x: p.x, y: p.y, button, clickCount: 1 });
  await sleep(150);
}
const typeText = async (t) => { for (const ch of t) await key(ch); };
const enterNow = async () => { await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", windowsVirtualKeyCode: 13 }); await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", windowsVirtualKeyCode: 13 }); };
// in the grid a digit types into the cell (like Sheets), so the tests switch tabs by their buttons there
const toTab = (t) => js(`document.querySelector('#tabs [data-tab=${t}]').click(); return 1`);
const settled = () => until("!document.querySelector('#g-saved') || document.querySelector('#g-saved').textContent !== 'Saving…'", 8000);
const focus = () => js("const a = document.activeElement; return a ? a.tagName + '.' + a.className + '|' + (a.id || a.dataset.fk || a.textContent.trim().slice(0, 20)) : 'none'");
const popOpen = () => js("return !document.querySelector('#pop').hidden");
const chips = () => js("return Object.fromEntries([...document.querySelectorAll('[aria-label=\"Filter by status\"] .chip')].map((c) => [c.dataset.k || 'all', c.textContent]))");
const total = () => js("return +document.querySelector('#t-h .n').textContent");
const badge = () => js("return document.querySelector('#tabs [data-tab=followups] .n').textContent");
const mark = () => js("const w = document.querySelector('.g-wrap, .t-cards, .fu-view'); w.dataset.mark = '1'; return 1");
const stillMarked = () => js("return document.querySelector('.g-wrap, .t-cards, .fu-view')?.dataset.mark === '1'");
const tips = async (name, scope, ignore = "") => { const miss = await missingTips(js, scope, ignore); check(`labels: ${name}`, miss.length === 0, miss.join(" || ")); };
// an in-page click timed until pred holds (synchronous DOM changes are seen in the same tick)
const timedClick = (sel, text, pred) => js(`const el = [...document.querySelectorAll(${JSON.stringify(sel)})].find((x) => x.offsetParent !== null && (${JSON.stringify(text)} === "" || x.textContent.trim().startsWith(${JSON.stringify(text)})));
  if (!el) return null; el.focus(); const t0 = performance.now(); el.click();
  return await new Promise((r) => { const tick = () => { if (${pred}) r(Math.round(performance.now() - t0)); else if (performance.now() - t0 > 4000) r(null); else requestAnimationFrame(tick); }; tick(); })`);
const press = (sel) => js(`const el = [...document.querySelectorAll(${JSON.stringify(sel)})].find((x) => x.offsetParent !== null); el?.focus(); el?.click(); return !!el`);
const mouse = async (x, y) => {
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button: "left", clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button: "left", clickCount: 1 });
};
const todayMinus = (days) => new Date(Date.now() - days * 86400000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const post = (path, body) => js(`return (await (await fetch(${JSON.stringify(path)}, { method: "POST", headers: { "Content-Type": "application/json", "X-Jobhunt": "1" }, body: JSON.stringify(${JSON.stringify({ profile: P, ...body })}) })).json())`);

// resumegen GOAL3 §7: a new open always starts on Recommended, so the tracker is reached by its tab (top or bottom bar)
const tracker = async () => {
  await until("document.body.dataset.view === 'jobs' && document.querySelector('#tabs [aria-selected=true]')", 15000);   // app routed
  await js("[...document.querySelectorAll('[data-tab=tracker]')].find((b) => b.offsetParent).click()");
};
try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/*" }] });
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:${P}` });
  await tracker();                                 // resumegen GOAL3 §7: an open starts on Recommended
  check("the tracker loads", await until("document.querySelectorAll('.g tbody tr[data-app]').length > 0 && document.querySelector('#tabs [data-tab=followups] .n')?.textContent === '1'", 15000), await badge());
  await sleep(1800);                                         // tip.js and rg.js load after the first paint
  await tips("tracker table", "#view");

  // ---- scroll fps (GOAL7's check still holds) ----
  const fps = await js(`const wrap = document.querySelector('.g-wrap'); let frames = 0; const t0 = performance.now();
    await new Promise((done) => { const step = () => { frames++; wrap.scrollTop += 40; performance.now() - t0 < 1500 ? requestAnimationFrame(step) : done(); }; requestAnimationFrame(step); });
    return Math.round(frames / ((performance.now() - t0) / 1000))`);
  check("scrolling 1200 rows stays smooth", fps >= 55, `${fps} fps`);
  await js("const w = document.querySelector('.g-wrap'); w.scrollTop = 0; w.dispatchEvent(new Event('scroll'))");
  await sleep(300);

  // ---- the search box keeps its focus and text while the list reloads ----
  await js("document.querySelector('#t-search').focus()");
  await key("C");
  await key("o");
  await sleep(600);
  check("typing in the search keeps the focus across the reload", (await focus()).startsWith("INPUT.|t-search") && (await js("return document.querySelector('#t-search').value")) === "Co", await focus());
  check("a searched Tracker shows no stray null", await js("return ![...document.querySelector('.g-wrap')?.childNodes || []].some((n) => n.nodeType === 3 && /null/.test(n.textContent))"));
  await js("const s = document.querySelector('#t-search'); s.value = ''; s.dispatchEvent(new Event('input'))");
  await until("document.querySelectorAll('.g tbody tr[data-app]').length > 10", 4000);

  // ---- a status chip applies at once and keeps the focus ----
  const ms0 = await timedClick('[aria-label="Filter by status"] .chip', "Saved", "document.querySelector('[aria-label=\"Filter by status\"] .chip[data-k=saved]').getAttribute('aria-pressed') === 'true'");
  check("a status chip shows as pressed at once", ms0 != null && ms0 < FAST_MS, `${ms0} ms`);
  const ST = await colOf("Status");
  check("the Saved filter lists the saved rows", await until(`[...document.querySelectorAll('${G_ROWS}')].length === 3 && [...document.querySelectorAll('${G_ROWS}')].every((tr) => tr.children[${ST} + 1].textContent.startsWith('Saved'))`, 4000));
  check("the chip keeps the focus after the reload", (await focus()).includes("Filter by status:saved"), await focus());

  // ---- a status change in the grid: row, counts and total at once, no reload; ⌘Z; rollback on a 500 ----
  const saved = await js(`return [...document.querySelectorAll('${G_ROWS}')].map((r) => r.dataset.app)`);
  const app = saved[0];
  const before = await chips();
  await mark();
  await pressCell(app, ST);
  await typeText("Screen");
  check("the status cell lists the statuses as you type", (await js("return [...document.querySelectorAll('.g-ed .g-opt')].map((o) => o.textContent).join()")) === "Screen");
  await tips("the status list", ".g-ed");
  await enterNow();
  check("a status change takes the row out of the Saved filter and counts it at once", await until(`!document.querySelector('${row(app)}') && document.querySelector('[aria-label="Filter by status"] .chip[data-k=screen]').textContent === 'Screen 1'`, FAST_MS));
  const after = await chips();
  check("the status counts follow", after.saved === "Saved 2" && after.screen === "Screen 1" && before.saved === "Saved 3", JSON.stringify(after));
  check("the total follows", (await total()) === 2, await total());
  check("the table was not reloaded", await stillMarked());
  await settled(); await sleep(300);
  check("the server has the move", (await server(`/api/tracker/row?profile=${P}&app=${app}`)).status === "screen");
  await js("document.querySelector('.g-wrap').focus(); return 1");
  await key("z", 4);
  check("⌘Z brings the row back where it was", await until(`document.querySelector('${row(app)}')?.children[${ST} + 1].textContent.startsWith('Saved') && document.querySelectorAll('${G_ROWS}').length === 3`, 4000));
  check("and the counts and total with it", (await chips()).saved === "Saved 3" && (await total()) === 3 && (await chips()).screen === "Screen 0", JSON.stringify(await chips()));
  await settled(); await sleep(300);
  check("the server undid it", (await server(`/api/tracker/row?profile=${P}&app=${app}`)).status === "saved");
  state.failNext = "/api/tracker/update_many";
  await pressCell(app, ST);
  await typeText("Interview");
  await enterNow();
  await until("document.querySelector('.toast.err')", 4000);
  await sleep(200);
  check("a refused change is put back with a message", (await cellText(app, ST))?.startsWith("Saved")
    && (await chips()).interview === "Interview 0" && (await js("return document.querySelector('.toast.err p').textContent")).includes("Put back"), JSON.stringify([await cellText(app, ST), await chips()]));
  check("the grid keeps the focus", (await focus()).includes("g-wrap"), await focus());

  // ---- Mark applied on a saved row (right-click; the dialog used to crash) ----
  await pressCell(app, 0, "right");
  check("right-click offers Mark applied…", await until("[...document.querySelectorAll('#pop .opt')].some((o) => o.textContent.startsWith('Mark applied'))", 2000));
  await tips("the row menu", "#pop");
  const ms2 = await timedClick("#pop .opt", "Mark applied", "document.querySelector('#apply-dlg[open]')");
  check("Mark applied opens its dialog", ms2 != null && ms2 < FAST_MS, `${ms2} ms`);
  await tips("Mark applied dialog", "#apply-dlg");
  const ms3 = await timedClick("#ad-without", "", `!document.querySelector('${row(app)}')`);
  check("Mark applied without a file takes the row out of Saved at once", ms3 != null && ms3 < FAST_MS, `${ms3} ms`);
  check("the counts follow Mark applied", (await chips()).saved === "Saved 2" && (await chips()).applied === "Applied 1202", JSON.stringify(await chips()));
  check("the server has it applied", await until(`(async () => (await (await fetch('/api/tracker/row?profile=${P}&app=${app}')).json()).status === 'applied')()`, 4000));
  check("focus stays in the table after the dialog", !(await focus()).startsWith("BODY"), await focus());

  // ---- Show columns: a live multi-choice menu ----
  await click('[aria-label="Filter by status"] .chip[data-k=""]');
  await until(`document.querySelectorAll('${G_ROWS}').length > 10`, 4000);
  await js("document.querySelector('.g-wrap').scrollTop = 600; return 1");
  await sleep(300);
  const HEADS = "return document.querySelectorAll('.g thead th[data-j]').length";
  const cols = await js(HEADS);
  await clickText(".t-acts .btn", "Columns");
  check("Columns opens the menu", await popOpen());
  await tips("Show columns menu", "#pop");
  await js("document.querySelectorAll('#pop input[type=checkbox]')[2].click()");
  await sleep(200);
  check("a tick hides the column at once and the menu stays open", await popOpen() && (await js(HEADS)) === cols - 1);
  check("the table keeps its scroll position", (await js("return document.querySelector('.g-wrap').scrollTop")) === 600, await js("return document.querySelector('.g-wrap').scrollTop"));
  check("the anchor still says it is open", (await js("return [...document.querySelectorAll('.t-acts .btn')].find((b) => b.textContent.includes('Columns')).getAttribute('aria-expanded')")) === "true");
  await js("document.querySelectorAll('#pop input[type=checkbox]')[3].click()");
  await sleep(200);
  check("a second tick applies too, menu still open", await popOpen() && (await js(HEADS)) === cols - 2);
  check("the ticks are remembered", (await js("return JSON.parse(localStorage.getItem('jh.cols.alex.main')).hidden.length")) === 2);
  await js("document.querySelectorAll('#pop input[type=checkbox]')[3].focus()");
  await key("Escape");
  check("Esc closes it from a checkbox and focus goes back to Columns", !(await popOpen()) && (await focus()).includes("cols"), await focus());
  await clickText(".t-acts .btn", "Columns");
  await mouse(700, 60);
  await sleep(200);
  check("an outside click closes it", !(await popOpen()));
  await clickText(".t-acts .btn", "Columns");
  await clickText(".t-acts .btn", "Columns");
  check("a second click on Columns closes it", !(await popOpen()));
  await clickText(".t-acts .btn", "Columns");
  await clickText("#pop .opt", "Reset columns");
  await sleep(200);
  check("Reset shows every column again", (await js(HEADS)) === cols);
  await key("Escape");
  await js("const w = document.querySelector('.g-wrap'); w.scrollTop = 0; w.scrollLeft = 0; return 1");
  await sleep(300);

  // ---- a cell edit: shown at once, ⌘Z restores, focus kept ----
  const NOTES = await colOf("Notes");
  const app2 = await js(`return document.querySelector('${G_ROWS}').dataset.app`);
  await pressCell(app2, NOTES);
  await key("c");
  check("typing edits the cell", await until("document.querySelector('.g-ed textarea, .g-ed input')"));
  await tips("a cell in edit mode", "#view");
  await typeText("alled back");
  await mark();
  await enterNow();
  check("Enter shows the new value at once", await until(`document.querySelector(${JSON.stringify(cell(app2, NOTES))})?.textContent.includes('called back') && !document.querySelector('.g-ed')`, FAST_MS), await cellText(app2, NOTES));
  check("the grid keeps the focus", (await focus()).includes("g-wrap"), await focus());
  check("no reload for a cell edit", await stillMarked());
  await settled(); await sleep(300);
  check("the server has the cell", (await server(`/api/tracker/row?profile=${P}&app=${app2}`)).notes === "called back");
  check("the corner says Saved", (await js("return document.querySelector('#g-saved').textContent")).startsWith("Saved"), await js("return document.querySelector('#g-saved').textContent"));
  await key("z", 4);
  check("⌘Z restores the cell", await until(`!document.querySelector(${JSON.stringify(cell(app2, NOTES))})?.textContent.includes('called back')`, 4000));
  await settled(); await sleep(300);
  check("and the server", (await server(`/api/tracker/row?profile=${P}&app=${app2}`)).notes !== "called back");
  check("focus is back on the grid after ⌘Z", (await focus()).includes("g-wrap"), await focus());
  state.failNext = "/api/tracker/update_many";
  await pressCell(app2, NOTES);
  await typeText("refused");
  await enterNow();
  await until("document.querySelector('.toast.err')", 4000);
  await sleep(200);
  check("a refused cell edit is put back", !(await cellText(app2, NOTES)).includes("refused"), await cellText(app2, NOTES));

  // ---- the row editor (right-click): save paints the row and the Follow-ups badge, no reload; focus returns ----
  await pressCell(app2, 0, "right");
  const msOpen = await js(`const b = [...document.querySelectorAll('#pop .opt')].find((o) => o.textContent.startsWith('Open row editor')); const t0 = performance.now(); b.click();
    return await new Promise((r) => { const mo = new MutationObserver(() => { if (document.querySelector('#detail form.editor')) { mo.disconnect(); r(Math.round(performance.now() - t0)); } }); mo.observe(document.getElementById('detail'), { childList: true, subtree: true }); setTimeout(() => r(null), 5000); })`);
  check("the row editor opens quickly (lazy module + row)", msOpen != null && msOpen < 400, `${msOpen} ms`);
  await sleep(300);
  await tips("the row editor", "#detail", ".finder *, .finder");   // the Find contacts section is area C's file
  await mark();
  await js(`const f = document.querySelector('#detail form.editor'); f.querySelector('#ed-status').value = 'applied'; f.elements.date_applied.value = '${todayMinus(3)}'; f.elements.notes.value = 'from the editor'; f.querySelector('.btn.primary').focus(); f.requestSubmit()`);
  check("Save paints the row in the table", await until(`document.querySelector(${JSON.stringify(cell(app2, NOTES))})?.textContent.includes('from the editor')`, 4000));
  check("Save paints the Follow-ups badge", await until("document.querySelector('#tabs [data-tab=followups] .n').textContent === '2'", 4000), await badge());
  check("no reload for an editor save", await stillMarked());
  check("the editor keeps the focus on Save", (await focus()).includes("Save"), await focus());
  check("the editor shows the saved date", (await js("return document.querySelector('#detail form.editor').elements.date_applied.value")) === todayMinus(3));
  await key("Escape");
  await sleep(300);
  check("Esc closes the editor and focus returns to the grid", !(await js("return document.body.classList.contains('panel-open')")) && (await focus()).includes("g-wrap"), await focus());

  // ---- a table tab's rows leave at once: Applied is cards by day since 2026-10-16 (Tracker and Interviewing keep
  // the table), so this runs on Interviewing: a row moved to Rejected leaves at once; ⌘Z brings it back ----
  await toTab("interviewing");
  await until("document.querySelector('#t-h')?.textContent.startsWith('Interviewing')", 8000);
  await sleep(300);
  check("Interviewing is empty here", (await js("return document.querySelector('#view').textContent")).includes("Nothing here yet: a row moves here when its status becomes Screen"));
  await tips("Interviewing", "#view");
  const app3 = (await server(`/api/tracker?profile=${P}&status=applied`)).rows[5].id;
  await post("/api/tracker/update", { app: app3, changes: { status: "interview" } });
  await toTab("tracker"); await sleep(300); await toTab("interviewing");
  await until(`document.querySelector('${row(app3)}')`, 8000);
  await sleep(300);
  const n3 = await total();
  const ST3 = await colOf("Status");
  await mark();
  await pressCell(app3, ST3);
  await typeText("Rejected");
  await enterNow();
  check("on Interviewing a row moved to Rejected leaves at once", await until(`!document.querySelector('${row(app3)}')`, FAST_MS) && (await total()) === n3 - 1, `total ${await total()} of ${n3}`);
  check("without a reload", await stillMarked());
  await settled(); await sleep(300);
  await js("document.querySelector('.g-wrap').focus(); return 1");
  await key("z", 4);
  check("⌘Z puts it back on Interviewing", await until(`document.querySelector('${row(app3)}') && +document.querySelector('#t-h .n').textContent === ${n3}`, 4000), await total());
  await settled(); await sleep(300);
  await post("/api/tracker/update", { app: app3, changes: { status: "applied" } });   // the rest sees the seed as before

  // ---- Follow-ups: the due list, Mark sent in place, the "Did you apply?" list ----
  const savedRow = (await server(`/api/tracker?profile=${P}&status=saved`)).rows[0].id;
  const pend = await post("/api/apply_click", { app: savedRow });
  await post("/api/pending/answer", { id: pend.pending, answer: "later" });
  await toTab("followups");
  const DUE_ROWS = ".fu-group.due-overdue .fu-row, .fu-group.due-today .fu-row";
  check("Follow-ups lists the due rows", await until(`document.querySelectorAll('${DUE_ROWS}').length === 2`, 8000), await js(`return document.querySelectorAll('${DUE_ROWS}').length`));
  check("and the Later one to answer", await until("document.querySelector('.back-later .btn')", 6000));
  await sleep(300);
  await tips("Follow-ups", "#view");
  check("the badge counts both", (await badge()) === "3", await badge());
  await clickText(".back-later .btn", "Answer now");
  check("Answer now asks", await until("document.querySelector('dialog.back-dlg[open]')?.textContent.includes('Did you apply')", 4000));
  await clickText("dialog.back-dlg .btn", "No");
  check("No takes it off the list and the badge", await until("!document.querySelector('.back-later .btn') && document.querySelector('#tabs [data-tab=followups] .n').textContent === '2'", 6000), await badge());
  await mark();
  const rows0 = await js(`return [...document.querySelectorAll('${DUE_ROWS}')].map((r) => r.dataset.app)`);
  const ms5 = await timedClick(`.fu-row[data-app="${rows0[0]}"] .btn`, "Mark sent today", `document.querySelectorAll('.fu-row[data-app="${rows0[0]}"] .fu').length < 2 || !document.querySelector('.fu-row[data-app="${rows0[0]}"]')`);
  check("Mark sent today updates the list at once", ms5 != null && ms5 < FAST_MS, `${ms5} ms`);
  check("without a reload", await stillMarked());
  check("focus stays in the view", !(await focus()).startsWith("BODY"), await focus());
  await until("[...document.querySelectorAll('.toast p')].some((p) => p.textContent.includes('marked sent'))");
  check("the badge follows", ((await badge()) || "0") === String(await js(`return document.querySelectorAll('${DUE_ROWS}').length`)), await badge());
  const badgeAfter = await badge();
  await js("[...document.querySelectorAll('.toast')].find((t) => t.textContent.includes('marked sent'))?.querySelector('button.undo')?.click()");
  check("GOAL33: Undo puts the follow-up back as due", await until(`[...document.querySelectorAll('${DUE_ROWS}')].some((r) => r.dataset.app === "${rows0[0]}")
    && [...document.querySelectorAll('.toast p')].some((p) => p.textContent.includes('due again'))`, 6000), `${badgeAfter} -> ${await badge()}`);
  await mark();

  // ---- Stats, Archived (the application lines open the editor), Saved ----
  await toTab("stats");
  check("Stats draws", await until("document.querySelector('.stats .tiles')", 8000));
  await tips("Stats", "#view");
  await toTab("archived");
  check("Archived lists the rejected application", await until("document.querySelector('.apps .app-row')", 15000));
  await sleep(300);
  await tips("Archived application lines", "#list-more");
  await click(".apps .app-row");
  check("an archived line opens the row editor", await until("document.querySelector('#detail form.editor')", 5000));
  await key("Escape");
  await toTab("saved");
  await until("document.querySelector('#list-head h1')?.textContent.startsWith('Saved') && !document.querySelector('#list[aria-busy]')", 15000);
  await sleep(300);
  await tips("Saved (outside the job rows)", "#list-more, #list-head", JOB_ROWS);

  // ---- hover labels: hover, keyboard focus ----
  await toTab("tracker");
  await until("document.querySelectorAll('.g tbody tr[data-app]').length > 0", 8000);
  await sleep(300);
  for (const [sel, text] of [[".g thead th[data-j] .g-hm", "Sort, filter, hide"], [".g thead .g-rz", "Drag to resize the column"],
    [".g thead th[data-j]", "click selects the column"], [".t-acts .btn[data-fk=cols]", "Show or hide columns"]]) {
    await js(`document.querySelector(${JSON.stringify(sel)})?.scrollIntoView({ block: 'center', inline: 'center' })`);
    await sleep(200);
    const t = await hoverTip(send, js, sleep, sel);
    check(`hover label: ${text || sel}`, t.found && t.text.trim() && t.text.includes(text) && !t.overlaps, JSON.stringify(t));
  }
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 5, y: 5 });
  await js("document.querySelector('#t-search').focus()");
  await key("Tab");
  await sleep(450);
  check("a keyboard focus shows the label", (await js("const t = document.getElementById('tip'); return t && !t.hidden ? t.textContent : ''")).includes("Add a job"), await focus());
  await pressCell(await js(`return document.querySelector('${G_ROWS}').dataset.app`), 0, "right");
  await clickText("#pop .opt", "Open row editor");
  await until("document.querySelector('#detail form.editor')", 5000);
  await js("document.querySelector('#detail form.editor .btn.primary').scrollIntoView({ block: 'center' })");
  await sleep(200);
  const te = await hoverTip(send, js, sleep, "#detail form.editor .btn.primary");
  check("hover label on an editor button", te.found && te.text === "Save (Enter)" && !te.overlaps, JSON.stringify(te));
  await key("Escape");

  // ---- phone: cards, Move to…, the More menu, long press ----
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await send("Emulation.setTouchEmulationEnabled", { enabled: true });
  await send("Page.navigate", { url: `${BASE}/?p=1#recommended:${P}` });
  await tracker();                                 // resumegen GOAL3 §7: an open starts on Recommended
  check("the phone shows cards", await until("document.querySelectorAll('.t-card[data-app]').length > 0", 15000));
  await sleep(1800);
  await tips("phone cards", "#view");
  const wide = await js("return [...document.querySelectorAll('.t-card *')].filter((e) => e.getBoundingClientRect().right > innerWidth + 1).map((e) => e.className + ':' + e.textContent.slice(0, 30))");
  check("nothing in a card runs past 390 px (long company name included)", wide.length === 0, wide.join(" | "));
  const small = await js("return [...document.querySelectorAll('#view button, #view input, #view a')].filter((e) => e.offsetParent !== null).map((e) => [e.className, e.getBoundingClientRect().height]).filter(([, h]) => h < 44)");
  check("every control on the phone is 44 px tall", small.length === 0, JSON.stringify(small));
  const cardApp = await js("return document.querySelector('.t-card[data-app] .st').closest('.t-card').dataset.app");
  await mark();
  await press(`.t-card[data-app="${cardApp}"] .st`);
  const ms6 = await timedClick("#pop .opt", "Cold", `document.querySelector('.t-card[data-app="${cardApp}"] .st')?.textContent.startsWith('Cold') && getComputedStyle(document.querySelector('.t-card[data-app="${cardApp}"] .st'), '::before').content === '"◌"'`);
  check("Move to… on a card paints at once", ms6 != null && ms6 < FAST_MS && (await stillMarked()), `${ms6} ms`);
  check("and the card keeps the focus", (await focus()).startsWith("BUTTON.st"), await focus());
  await until("[...document.querySelectorAll('.toast p')].some((p) => p.textContent.includes('moved to Cold'))");
  await js("[...document.querySelectorAll('.toast')].find((t) => t.textContent.includes('moved to Cold')).querySelector('.undo').click()");
  await until(`!document.querySelector('.t-card[data-app="${cardApp}"] .st')?.textContent.includes('Cold')`, 4000);
  const r = await js(`const b = document.querySelector('.t-card[data-app="${cardApp}"] .st').getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }`);
  await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: r.x, y: r.y }] });
  await sleep(650);
  check("a long press shows the label on the phone", (await js("const t = document.getElementById('tip'); return t && !t.hidden ? t.textContent : ''")) === "Move to another status");
  await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await sleep(100);
  check("release hides it", await js("return document.getElementById('tip').hidden"));
  await key("Escape");
  await click('#subtabs [data-sub="followups"]');   // Applications' views are chips under the top bar on the phone
  check("the Applications strip opens Follow-ups on the phone", await until("document.querySelector('.fu-view')", 8000));
  await sleep(300);
  const link = await js("const b = document.querySelector('.fu-row .linklike'); return b ? b.getBoundingClientRect().height : 0");
  check("the follow-up links are 44 px tall on the phone", link >= 44, link);

  // ---- jordan: the vendors tab edits in place ----
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${BASE}/?p=2#recommended:jordan` });
  await tracker();                                 // resumegen GOAL3 §7: an open starts on Recommended
  await until("document.querySelectorAll('.g tbody tr[data-app]').length > 0", 15000);
  await sleep(500);
  await clickText('[aria-label="Sheet tab"] .chip', "Sheet2");
  check("the vendors tab draws", await until("document.querySelector('.t.vendors tbody tr')", 8000));
  await tips("vendors", "#view");
  await click(".t.vendors tbody tr td.editable");
  await until("document.querySelector('.t.vendors td.editing input')");
  await js("const i = document.querySelector('.t.vendors td.editing input'); i.value = 'Acme Staffing'; i.focus()");
  await key("Enter");
  check("a vendor cell shows the new value at once and keeps the focus", (await js("return document.querySelector('.t.vendors tbody tr td.editable').textContent")) === "Acme Staffing" && (await focus()).startsWith("TD."), await focus());
  await until("[...document.querySelectorAll('.toast p')].some((p) => p.textContent.includes('Vendor saved'))");
  check("the server has the vendor", (await server("/api/vendors?profile=jordan")).vendors[0].company === "Acme Staffing");
  await click(".t.vendors tbody tr td.editable");
  await until("document.querySelector('.t.vendors td.editing input')");
  check("the next edit starts from the saved value", (await js("return document.querySelector('.t.vendors td.editing input').value")) === "Acme Staffing");
  await key("Escape");
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || err);
} finally {
  console.log(JSON.stringify({ checks }));
  close();
}

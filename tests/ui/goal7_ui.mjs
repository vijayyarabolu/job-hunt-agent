// GOAL7 §5 UI checks in headless Chrome (DevTools protocol, no packages): keyboard shortcuts, optimistic actions with
// Undo (and rollback when the server says no), filter chips. Run by tests/test_goal7_ui.py against a throwaway DB.
// usage: node goal7_ui.mjs <base url> <token> <chrome path> <profile dir>  -> prints {"checks": [{name, ok, detail}]}
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR] = process.argv.slice(2);
const { send, js, until, key, errors, checks, state, check, click, clickText, server, close } = await start(CHROME, PROFILE_DIR);
const selected = () => js("return document.querySelector('#list [aria-selected=true]')?.id");
const tab = () => js("return document.querySelector('#tabs [aria-selected=true]')?.dataset.tab");

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/*" }] });
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:alex` });
  check("feed loads", await until("document.querySelectorAll('#list .row[data-i]').length >= 3"));
  const uid0 = await js("return (await (await fetch('/api/jobs?profile=alex&tab=recommended')).json()).jobs[0].uid");

  // ---- keyboard ----
  await until("document.querySelector('#detail .btn')");          // the detail is drawn, the first row scrolled to
  await key("Tab");
  check("the first Tab goes to the skip link", await js("return document.activeElement.classList.contains('skip')"),
    await js("return document.activeElement.className || document.activeElement.tagName"));
  await js("document.activeElement?.blur(); document.body.focus()");
  await key("j");
  check("j selects the next job", (await selected()) === "job-1", await selected());
  await key("k");
  check("k selects the previous job", (await selected()) === "job-0", await selected());
  await key("ArrowDown");
  check("↓ moves like j", (await selected()) === "job-1", await selected());
  await key("k");
  await key("?");
  check("? opens the keyboard sheet", await js("return document.querySelector('#help').open"));
  await key("Escape");
  check("Esc closes it", await js("return !document.querySelector('#help').open"));
  await key("2");
  check("2 switches to Saved", await until("document.querySelector('#tabs [data-tab=saved]').getAttribute('aria-selected') === 'true'"), await tab());
  await key("1");
  await until("document.querySelectorAll('#list .row[data-i]').length >= 3");
  check("1 switches back to Recommended", (await tab()) === "recommended", await tab());
  await key("k", 4);                        // ⌘K (meta)
  check("⌘K opens the palette", await js("return document.querySelector('#palette').open"));
  await js("const q = document.querySelector('#pal-q'); q.value = 'track'; q.dispatchEvent(new Event('input'))");
  await key("Enter");
  check("palette jumps to Tracker", await until("document.querySelector('#tabs [data-tab=tracker]').getAttribute('aria-selected') === 'true'"), await tab());
  await key("1");
  await until("document.querySelectorAll('#list .row[data-i]').length >= 3");
  await key("Enter");
  check("Enter opens the job in the detail pane", await until("document.querySelector('#detail .d-title')"));

  // ---- optimistic actions + Undo ----
  await js("document.activeElement?.blur()");
  await key("s");
  check("s marks the row Saved at once", await js("return document.querySelector('#job-0 .tag:not(.yrs)')?.textContent === 'Saved'"));
  await until("[...document.querySelectorAll('.toast p')].some((p) => p.textContent.startsWith('Saved:'))");
  const savedNow = (await server("/api/jobs?profile=alex&tab=saved")).jobs.map((j) => j.uid);
  check("the server has it saved", savedNow.includes(uid0), savedNow);
  check("the toast offers Undo", await click(".toast .undo"));
  await sleep(700);
  const savedAfter = (await server("/api/jobs?profile=alex&tab=saved")).jobs.map((j) => j.uid);
  check("Undo un-saves it on the server", !savedAfter.includes(uid0), savedAfter);
  check("Undo puts the row back", await js("return !document.querySelector('#job-0 .tag:not(.yrs)')"));

  const before = await js("return document.querySelectorAll('#list .row[data-i]').length");
  await key("x");
  await sleep(400);
  check("x removes the row at once", (await js("return document.querySelectorAll('#list .row[data-i]').length")) === before - 1);
  await until("document.querySelector('.toast .why')");
  check("the toast asks why", await js("return document.querySelectorAll('.toast .why .chip').length === 5"));
  check("Undo for not interested", await click(".toast .undo"));
  await sleep(700);
  check("the row is back", (await js("return document.querySelectorAll('#list .row[data-i]').length")) === before);
  const archived = (await server("/api/jobs?profile=alex&tab=archived")).jobs.map((j) => j.uid);
  check("the server forgot the pass", !archived.includes(uid0), archived);

  state.failNext = "/api/triage";
  await key("s");
  await until("document.querySelector('.toast.err')");
  check("a refused save rolls back with a message",
    await js("return document.querySelector('.toast.err p').textContent.includes('Put back') && !document.querySelector('#job-0 .tag:not(.yrs)')"));

  // ---- filter chips ----
  await clickText("#fchips .chip", "Workplace");
  check("a filter chip opens its popover", await js("return !document.querySelector('#pop').hidden"));
  await clickText("#pop .opt", "Remote");
  check("the choice shows as an active chip", await until("document.querySelector('#fchips .chip.on')?.textContent.includes('Workplace: Remote')"));
  check("it is not stored for the next open", await js("return localStorage.getItem('jh.filters.alex') === null"));   // resumegen GOAL3 §7
  await click("#fchips .chip.on .chip-x");
  check("× removes the chip", await js("return !document.querySelector('#fchips .chip.on')"));
  check("the toast offers Undo for the filter", await until("[...document.querySelectorAll('.toast p')].some((p) => p.textContent.startsWith('Removed'))"));
  await js("[...document.querySelectorAll('.toast')].find((t) => t.textContent.includes('Removed')).querySelector('.undo').click()");
  check("Undo brings the filter back", await until("document.querySelector('#fchips .chip.on')?.textContent.includes('Workplace: Remote')"));
  await clickText("#fchips .seg button", "24h");
  check("Posted is one control", await js("return document.querySelector('#fchips .seg [aria-pressed=true]')?.textContent === '24h'"));
  await clickText("#fchips .chip", "More");
  const more = await js("return document.querySelector('#pop').textContent");
  check("More lists every extra filter", ["Experience asked", "Company size", "Type", "Show too senior", "Hide applied"]
    .every((t) => more.includes(t)) && !more.includes("[object"), more.slice(0, 160));
  await clickText("#pop .opt", "Startup");
  check("a More choice becomes an active chip", await until("[...document.querySelectorAll('#fchips .chip.on')].some((c) => c.textContent.includes('Company size: Startup'))"));

  // ---- tracker: the Sheets-like grid (tests/ui/goal19_ui.mjs has every key): virtualized, sticky,
  // the status list with ⌘Z, columns hidden / resized and remembered, typing edits in place ----
  await key("5");                                          // the side menu's order (Tracker is 5th)
  check("5 opens the tracker table", await until("document.querySelectorAll('.g tbody tr[data-app]').length > 0", 10000));
  const drawn = await js("return { rows: document.querySelectorAll('.g tbody tr[data-app]').length, total: +document.querySelector('.g').getAttribute('aria-rowcount') - 1, height: document.querySelector('.g-wrap').scrollHeight }");
  check("only the rows near the view are drawn", drawn.total >= 1000 && drawn.rows <= 120 && drawn.height > drawn.total * 24, JSON.stringify(drawn));
  const firstBefore = await js("return document.querySelector('.g tbody tr[data-app]').dataset.app");
  const fps = await js(`const wrap = document.querySelector('.g-wrap'); let frames = 0; const t0 = performance.now();
    await new Promise((done) => { const step = () => { frames++; wrap.scrollTop += 40; performance.now() - t0 < 1500 ? requestAnimationFrame(step) : done(); }; requestAnimationFrame(step); });
    return Math.round(frames / ((performance.now() - t0) / 1000))`);
  await sleep(300);
  const firstAfter = await js("return document.querySelector('.g tbody tr[data-app]').dataset.app");
  check("scrolling draws the rows further down", firstAfter !== firstBefore, `${firstBefore} -> ${firstAfter}`);
  check("scrolling 1000+ rows stays smooth", fps >= 55, `${fps} fps`);
  check("the header stays put", await js("const wrap = document.querySelector('.g-wrap'); const th = document.querySelector('.g thead th[data-j=\"1\"]'); return Math.abs(th.getBoundingClientRect().top - wrap.getBoundingClientRect().top) < 3"));
  check("the first column stays put", await js("const wrap = document.querySelector('.g-wrap'); wrap.scrollLeft = 600; await new Promise((r) => setTimeout(r, 100)); const rn = document.querySelector('.g tbody tr[data-app] .g-rn'); return Math.abs(rn.getBoundingClientRect().left - wrap.getBoundingClientRect().left) < 3"));
  await js("const wrap = document.querySelector('.g-wrap'); wrap.scrollTop = 0; wrap.scrollLeft = 0; await new Promise((r) => setTimeout(r, 300)); return true");

  const colOf = (label) => js(`return [...document.querySelectorAll('.g thead th[data-j]')].findIndex((th) => th.querySelector('.g-hl').firstChild.textContent === ${JSON.stringify(label)})`);
  const pressAt = async (i, j) => {
    const p = await js(`const td = document.querySelector('.g tr[data-i="${i}"]').children[${j} + 1]; td.scrollIntoView({ block: 'nearest', inline: 'center' }); await new Promise((r) => setTimeout(r, 150));
      const r = td.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }`);
    lastPress = { ...p, at: await js(`return document.elementFromPoint(${p.x}, ${p.y})?.outerHTML.slice(0, 120)`) };
    for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) await send("Input.dispatchMouseEvent", { type, x: p.x, y: p.y, button: "left", clickCount: 1 });
    await sleep(150);
  };
  let lastPress = null;
  const typeText = async (t) => { for (const ch of t) await key(ch); };
  const settled = () => until("!document.querySelector('#g-saved') || document.querySelector('#g-saved').textContent !== 'Saving…'", 8000);
  const st = await colOf("Status");
  const app = await js("return document.querySelector('.g tr[data-i=\"0\"]').dataset.app");
  await pressAt(0, st);
  await typeText("Interview");
  await key("Enter");
  check("Move to… changes the status at once", await js(`return document.querySelector('.g tr[data-i="0"]').children[${st} + 1].textContent.startsWith('Interview')`), JSON.stringify(lastPress));
  await settled(); await sleep(400);
  check("the server has the new status", (await server(`/api/tracker/row?profile=alex&app=${app}`)).status === "interview");
  await js("document.querySelector('.g-wrap').focus(); return true");
  await key("z", 4);
  await settled(); await sleep(600);
  check("Undo moves it back", (await server(`/api/tracker/row?profile=alex&app=${app}`)).status === "applied");

  const cols = await js("return document.querySelectorAll('.g thead th[data-j]').length");
  await js("document.querySelector('.g thead th[data-j=\"2\"] .g-hm').click(); return true");
  await sleep(250);
  await clickText("#pop .opt", "Hide column");
  await sleep(300);
  check("a column can be hidden", (await js("return document.querySelectorAll('.g thead th[data-j]').length")) === cols - 1);
  check("hidden columns are remembered", await js("return JSON.parse(localStorage.getItem('jh.cols.alex.main')).hidden.length === 1"));
  await js("document.querySelector('.g-wrap').scrollLeft = 0; return true");   // a narrower grid (side menu) may be scrolled sideways
  await sleep(100);
  const grip = await js(`const g = document.querySelectorAll('.g thead .g-rz')[1]; const r = g.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + 10) }`);
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x: grip.x, y: grip.y, button: "left", clickCount: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: grip.x + 80, y: grip.y, button: "left", buttons: 1 });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: grip.x + 80, y: grip.y, button: "left", clickCount: 1 });
  await sleep(200);
  check("a column can be resized and the width is remembered", await js("return Object.keys(JSON.parse(localStorage.getItem('jh.cols.alex.main')).widths).length === 1"),
    await js(`return [localStorage.getItem('jh.cols.alex.main'), document.elementFromPoint(${grip.x}, ${grip.y})?.outerHTML.slice(0, 100), document.scrollingElement.scrollLeft, document.body.scrollLeft].join(' | ')`));

  const notes = await colOf("Notes");
  await pressAt(0, notes);
  await key("c");
  check("a click edits the cell in place", await until("document.querySelector('.g-ed textarea, .g-ed input')"),
    await js(`return [${JSON.stringify(notes)}, document.querySelector('.g-wrap').scrollLeft, JSON.stringify(document.querySelector('.g tr[data-i="0"]').children[${notes} + 1].getBoundingClientRect()), document.activeElement?.className].join(' | ')`));
  await typeText("alled back");
  await key("Enter");
  await settled(); await sleep(400);
  check("Enter saves the cell", (await server(`/api/tracker/row?profile=alex&app=${app}`)).notes === "called back");
  await js("document.querySelector('.g-wrap').focus(); return true");
  await key("z", 4);
  await settled(); await sleep(600);
  check("Undo restores the cell", (await server(`/api/tracker/row?profile=alex&app=${app}`)).notes !== "called back");
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || err);
} finally {
  console.log(JSON.stringify({ checks }));
  close();
}

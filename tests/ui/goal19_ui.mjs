// GOAL19 §1 in headless Chrome against a throwaway DB (tests/test_goal19_ui.py): the Tracker grid, Sheets-like.
// usage: node goal19_ui.mjs <base url> <token> <chrome path> <profile dir> <expected json>
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXPECTED] = process.argv.slice(2);
const want = JSON.parse(EXPECTED);
const { send, js, until, errors, check, checks, close } = await start(CHROME, PROFILE_DIR);
const S = JSON.stringify;
const CODES = { ArrowDown: 40, ArrowUp: 38, ArrowRight: 39, ArrowLeft: 37, Tab: 9, Enter: 13, Escape: 27, Home: 36, End: 35,
  F2: 113, Delete: 46, Backspace: 8 };
const META = 4, SHIFT = 8;
let who = "alex";

async function press(k, mods = 0, commands) {
  const code = CODES[k] ?? k.toUpperCase().charCodeAt(0);
  const text = k === "Enter" ? "\r" : k.length === 1 && !(mods & (META | 2)) ? (mods & SHIFT ? k.toUpperCase() : k) : undefined;
  await send("Input.dispatchKeyEvent", { type: text ? "keyDown" : "rawKeyDown", key: k, windowsVirtualKeyCode: code, modifiers: mods, text, commands });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: k, windowsVirtualKeyCode: code, modifiers: mods });
  await sleep(140);
}
const type = async (t) => { await send("Input.insertText", { text: t }); await sleep(120); };
async function mouse(x, y, { mods = 0, button = "left", count = 1 } = {}) {
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x, y, modifiers: mods });
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x, y, button, clickCount: count, modifiers: mods });
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x, y, button, clickCount: count, modifiers: mods });
  await sleep(150);
}
async function drag(from, to) {
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: from.x, y: from.y });
  await send("Input.dispatchMouseEvent", { type: "mousePressed", x: from.x, y: from.y, button: "left", clickCount: 1 });
  for (let k = 1; k <= 3; k++) {                                       // a real drag passes through the cells between
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: Math.round(from.x + (to.x - from.x) * k / 3), y: Math.round(from.y + (to.y - from.y) * k / 3), button: "left", buttons: 1 });
    await sleep(30);
  }
  await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: to.x, y: to.y, button: "left", clickCount: 1 });
  await sleep(250);
}
const helpers = (pid) => `window.T19 = {
  cur() { const c = document.querySelector('#g-cur'); if (!c) return null; const tr = c.parentNode; return { i: +tr.dataset.i, j: [...tr.children].indexOf(c) - 1, text: c.textContent, app: +tr.dataset.app }; },
  col(name) { return [...document.querySelectorAll('.g thead th[data-j]')].findIndex((th) => th.querySelector('.g-hl').firstChild.textContent === name); },
  cell(i, j) { return document.querySelector('.g tr[data-i="' + i + '"]')?.children[j + 1] || null; },
  at(i, j) { const td = T19.cell(i, j); const w = document.querySelector('.g-wrap'); const wr = w.getBoundingClientRect(); let r = td.getBoundingClientRect();
    if (!td.classList.contains('fz') && (r.right > wr.right - 4 || r.left < wr.left + 420)) { w.scrollLeft += r.left - wr.left - 460; r = td.getBoundingClientRect(); }
    if (r.bottom > wr.bottom - 4 || r.top < wr.top + 30) { w.scrollTop += r.top - wr.top - 120; r = td.getBoundingClientRect(); } return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; },
  app(i) { return +document.querySelector('.g tr[data-i="' + i + '"]').dataset.app; },
  async row(app) { return (await fetch('/api/tracker/row?profile=${pid}&app=' + app)).json(); },
  ed() { return document.querySelector('.g-ed input:not([type=date]), .g-ed textarea'); },
  sel() { return document.querySelectorAll('.g td.sl').length; },
};
window.__opened = []; window.open = (u) => { window.__opened.push(u); return null; }; return true`;
const cur = () => js("return T19.cur()");
const col = (name) => js(`return T19.col(${S(name)})`);
const at = (i, j) => js(`return T19.at(${i}, ${j})`);
const appAt = (i) => js(`return T19.app(${i})`);
const serverRow = (app) => js(`return T19.row(${app})`);
const edValue = () => js("return T19.ed()?.value ?? null");
const cellText = (i, j) => js(`return T19.cell(${i}, ${j}).textContent`);
const settle = async () => { await sleep(150); await until("!document.querySelector('#g-saved') || document.querySelector('#g-saved').textContent !== 'Saving…'", 8000); await sleep(250); };
const OPEN_MS = `const t0 = performance.now(); document.querySelector('#tabs [data-tab=tracker]').click();
  for (let i = 0; i < 800; i++) { if (document.querySelector('.g tbody tr[data-app], .t-card')) return Math.round(performance.now() - t0); await new Promise((r) => setTimeout(r, 5)); } return -1`;

async function openTracker(pid) {
  who = pid;
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}&p=${pid}#recommended:${pid}` });   // a new URL: a full load, not a hash change
  await until("document.querySelector('#tabs [data-tab=tracker]') && document.querySelector('#who')", 10000);
  await sleep(800);
  const ms = await js(OPEN_MS);
  await js(helpers(pid));
  await sleep(300);
  return ms;
}
const focusGrid = () => js("document.querySelector('.g-wrap').focus(); return true");
async function clickCell(i, j, opts) { const p = await at(i, j); await mouse(p.x, p.y, opts); }
async function goCell(i, j) { await clickCell(i, j); return cur(); }
const paste = (text) => js(`const dt = new DataTransfer(); dt.setData('text/plain', ${S(text)});
  document.querySelector('.g-wrap').dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true })); return true`);
const copyEvent = (kind) => js(`const dt = new DataTransfer(); document.querySelector('.g-wrap').dispatchEvent(new ClipboardEvent('${kind}', { clipboardData: dt, bubbles: true, cancelable: true }));
  return dt.getData('text/plain')`);
const menuOf = async (name) => { await js(`document.querySelector('.g thead th[data-j="' + T19.col(${S(name)}) + '"] .g-hm').click(); return true`); await sleep(250); };
const pick = async (text) => { await js(`[...document.querySelectorAll('#pop .opt')].find((o) => o.textContent.startsWith(${S(text)}))?.click(); return true`); await sleep(450); };
const stateText = () => js("return document.querySelector('#g-state').textContent");
const stateBtn = (text) => js(`[...document.querySelectorAll('#g-state .mini')].find((b) => b.textContent === ${S(text)})?.click(); return true`);
const drawn = () => js("return document.querySelectorAll('.g tbody tr[data-app]').length");

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Browser.grantPermissions", { origin: BASE, permissions: ["clipboardReadWrite", "clipboardSanitizedWrite"] });
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });

  // ---- layout ----
  const first = await openTracker("alex");
  await js("document.querySelector('#tabs [data-tab=recommended]').click(); return true");
  await until("!document.querySelector('.g') && !document.querySelector('#list[aria-busy]')", 8000);
  await sleep(800);
  const warm = await js(OPEN_MS);
  await js(helpers("alex"));
  await sleep(300);
  check("the grid opens under 500 ms", warm >= 0 && warm < 500, `first open ${first} ms (modules fetched), again ${warm} ms`);
  const lay = await js(`const rows = [...document.querySelectorAll('.g tbody tr[data-app]')];
    return { drawn: rows.length, count: document.querySelector('.g').getAttribute('aria-rowcount'), heights: [...new Set(rows.map((r) => Math.round(r.getBoundingClientRect().height)))],
      rn: rows[0].firstChild.firstChild.textContent, rnClass: rows[0].firstChild.className }`);
  check("only the rows near the view are drawn", lay.drawn > 10 && lay.drawn < 120 && +lay.count === want.rows + 1, S(lay));
  check("row numbers on the left", lay.rn === "1" && lay.rnClass.includes("g-rn"), S(lay));
  check("every row has the same height", lay.heights.length === 1 && lay.heights[0] === 25, S(lay.heights));
  const stick = await js(`const w = document.querySelector('.g-wrap'); w.scrollTop = 6000; w.scrollLeft = 900; await new Promise((r) => setTimeout(r, 300));
    const wr = w.getBoundingClientRect(), th = document.querySelector('.g thead th[data-j="0"]').getBoundingClientRect();
    const tr = [...document.querySelectorAll('.g tbody tr[data-app]')].find((t) => t.getBoundingClientRect().top > wr.top + 40);
    const c0 = tr.children[1].getBoundingClientRect(), c1 = tr.children[2].getBoundingClientRect();
    return { headTop: Math.round(th.top - wr.top), c0: Math.round(c0.left - wr.left), gap: Math.round(c1.left - c0.right), first: tr.children[1].textContent }`);
  check("the header row stays put", Math.abs(stick.headTop - 1) <= 1, S(stick));
  check("Company and Role stay put", stick.c0 >= 45 && stick.c0 <= 48 && Math.abs(stick.gap) <= 1 && stick.first.startsWith("Company"), S(stick));
  const end = await js(`const w = document.querySelector('.g-wrap'); w.scrollTop = w.scrollHeight; await new Promise((r) => setTimeout(r, 400));
    return [...document.querySelectorAll('.g tbody tr[data-app]')].at(-1).firstChild.firstChild.textContent`);
  check("scrolling to the end draws the last rows", end === String(want.rows), end);
  await js("const w = document.querySelector('.g-wrap'); w.scrollTop = 0; w.scrollLeft = 0; await new Promise((r) => setTimeout(r, 300)); return true");
  const look = await js(`const th = document.querySelector('.g thead th[data-j="0"]'); const jd = T19.col('JD');
    const bg = (el) => el && getComputedStyle(el).backgroundColor; const file = [...document.querySelectorAll('.g .g-file')].map((x) => x.textContent);
    const conflict = document.querySelector('.g td.g-cf');
    return { head: bg(th), jd: bg(T19.cell(0, jd)), file, conflict: conflict && [conflict.textContent, conflict.title] }`);
  check("the Sheet's header color", look.head === "rgb(98, 110, 122)", S(look));
  check("the Sheet's green JD / Resume cells", look.jd === "rgb(183, 225, 205)", S(look));
  check("the Resume cell shows the Drive file chip", look.file.some((t) => t.includes("Resume(Company 3).pdf")), S(look.file));
  check("a conflict is flagged on its cell", !!look.conflict && look.conflict[0] === "Sheet Ann" && look.conflict[1].includes("App Ann"), S(look.conflict));

  // ---- selection & keys ----
  let c = await goCell(2, 2);
  check("a click selects the cell", c && c.i === 2 && c.j === 2, S(c));
  await press("ArrowDown"); c = await cur(); check("↓ moves down", c.i === 3 && c.j === 2, S(c));
  await press("ArrowRight"); c = await cur(); check("→ moves right", c.i === 3 && c.j === 3, S(c));
  await press("ArrowUp"); c = await cur(); check("↑ moves up", c.i === 2 && c.j === 3, S(c));
  await press("ArrowLeft"); c = await cur(); check("← moves left", c.i === 2 && c.j === 2, S(c));
  await press("ArrowDown"); await press("ArrowRight");
  await press("Tab"); c = await cur(); check("Tab moves right", c.j === 4, S(c));
  await press("Tab", SHIFT); c = await cur(); check("Shift+Tab moves left", c.j === 3, S(c));
  await press("Enter", SHIFT); c = await cur(); check("Shift+Enter moves up", c.i === 2, S(c));
  await press("Home"); c = await cur(); check("Home goes to the first column", c.j === 0, S(c));
  const lastCol = await js("return document.querySelectorAll('.g thead th[data-j]').length - 1");
  await press("End"); c = await cur(); check("End goes to the last column", c.j === lastCol, S([c, lastCol]));
  await press("Home");
  await press("ArrowDown", META); c = await cur(); check("⌘↓ jumps to the last row", c && c.i === want.rows - 1, S(c));
  await press("ArrowUp", META); c = await cur(); check("⌘↑ jumps to the first row", c && c.i === 0, S(c));
  await press("ArrowRight", META); c = await cur(); check("⌘→ jumps to the last column", c && c.j === lastCol, S(c));
  await press("ArrowLeft", META); c = await cur(); check("⌘← jumps to the first column", c && c.j === 0, S(c));
  await clickCell(1, 1);
  await clickCell(3, 2, { mods: SHIFT });
  check("Shift+click selects a range", (await js("return T19.sel()")) === 6, S([await js("return T19.sel()"), await cur()]));
  await clickCell(1, 1);
  await press("ArrowDown", SHIFT); await press("ArrowRight", SHIFT);
  check("Shift+arrows extend the range", (await js("return T19.sel()")) === 4, await js("return T19.sel()"));
  await drag(await at(1, 0), await at(4, 1));
  check("a drag selects a range", (await js("return T19.sel()")) === 8, await js("return T19.sel()"));
  const hd = await js("const r = document.querySelector('.g thead th[data-j=\"1\"] .g-hl').getBoundingClientRect(); return { x: r.left + 5, y: r.top + r.height / 2 }");
  await mouse(hd.x, hd.y);
  const colSel = await js("return { sl: T19.sel(), drawn: document.querySelectorAll('.g tbody tr[data-app]').length, head: document.querySelector('.g thead th[data-j=\"1\"]').classList.contains('hs') }");
  check("a header click selects the column", colSel.head && colSel.sl === colSel.drawn, S(colSel));
  await press("a", META);
  const all = await js("return { sl: T19.sel(), want: document.querySelectorAll('.g tbody tr[data-app]').length * document.querySelectorAll('.g thead th[data-j]').length }");
  check("⌘A selects everything", all.sl === all.want, S(all));

  // ---- editing ----
  const tn = await col("Target Name");
  await goCell(5, tn);
  await press("Z"); await type("ed Q");
  check("typing replaces the cell", (await edValue()) === "Zed Q", await edValue());
  await press("Enter");
  c = await cur();
  check("Enter saves and moves down", (await edValue()) === null && c.i === 6, S(c));
  await settle();
  const app5 = await appAt(5);
  check("the server has the typed value", (await serverRow(app5)).target_name === "Zed Q", (await serverRow(app5)).target_name);
  check("Saved shows in the corner", (await js("return document.querySelector('#g-saved').textContent")).startsWith("Saved"), await js("return document.querySelector('#g-saved').textContent"));
  await press("ArrowUp"); await press("Enter");
  check("Enter edits keeping the text", (await edValue()) === "Zed Q", await edValue());
  await type("!"); await press("Tab");
  c = await cur();
  check("Tab saves and moves right", c.j === tn + 1 && c.i === 5, S(c));
  await settle();
  await press("ArrowLeft"); await press("F2");
  check("F2 edits", (await edValue()) === "Zed Q!", await edValue());
  await type(" nope"); await press("Escape");
  check("Esc cancels", (await edValue()) === null && (await cellText(5, tn)) === "Zed Q!", await cellText(5, tn));
  const dbl = await at(5, tn);
  await mouse(dbl.x, dbl.y, { count: 2 });
  check("double-click edits", (await edValue()) === "Zed Q!", await edValue());
  await press("Escape");
  const st = await col("Status");
  await goCell(7, st);
  await press("r"); await type("ej");
  const listed = await js("return [...document.querySelectorAll('.g-ed .g-opt')].map((o) => o.textContent)");
  check("the status list filters as you type", S(listed) === S(["Rejected"]), S(listed));
  await press("Enter");
  check("Enter picks from the list", (await cellText(7, st)) === "Rejected", await cellText(7, st));
  await settle();
  const app7 = await appAt(7);
  check("the server has the picked status", (await serverRow(app7)).status === "rejected", (await serverRow(app7)).status);
  const year = new Date().getFullYear();
  const da = await col("Date Applied");
  await goCell(8, da);
  await press("1"); await type("0/12");
  check("the date picker is there", await js("return !!document.querySelector('.g-ed input[type=date]')"));
  await press("Enter");
  await settle();
  const app8 = await appAt(8);
  check("a typed 10/12 becomes a date", (await serverRow(app8)).date_applied === `10/12/${year}`, (await serverRow(app8)).date_applied);
  await press("t"); await type("oday"); await press("Enter");
  await settle();
  const app9 = await appAt(9);
  const today = await js("const d = new Date(); return String(d.getMonth() + 1).padStart(2, '0') + '/' + String(d.getDate()).padStart(2, '0') + '/' + d.getFullYear()");
  check("today becomes a date", (await serverRow(app9)).date_applied === today, (await serverRow(app9)).date_applied);
  const notes = await col("Notes");
  await goCell(10, notes);
  await press("Enter");
  check("long text opens a larger editor", await js("return !!document.querySelector('.g-ed textarea')"));
  await press("Enter", SHIFT); await type("second line");
  check("Shift+Enter is a new line", ((await edValue()) || "").endsWith("\nsecond line"), S(await edValue()));
  await press("Enter");
  await settle();
  const app10 = await appAt(10);
  check("the long text is saved with its line break", ((await serverRow(app10)).notes || "").endsWith("\nsecond line"), S((await serverRow(app10)).notes));
  const jd = await col("JD");
  await clickCell(0, jd, { mods: META });
  check("⌘-click opens a link", ((await js("return window.__opened.at(-1)")) || "").startsWith("https://jobs.example.com/"), await js("return JSON.stringify(window.__opened)"));

  // ---- clipboard, delete, undo, fill ----
  const tr2 = await col("Target Role");
  await clickCell(11, tn); await clickCell(12, tr2, { mods: SHIFT });
  const copied = await copyEvent("copy");
  check("⌘C copies the range as TSV", copied === `${await cellText(11, tn)}\t${await cellText(11, tr2)}\n${await cellText(12, tn)}\t${await cellText(12, tr2)}`, S(copied));
  await clickCell(11, tn);
  await paste('Ann Lee\tRecruiter\n"Bo ""B"" Chan\nsecond line"\tHiring Manager\n');
  await settle();
  const app11 = await appAt(11), app12 = await appAt(12);
  const r11 = await serverRow(app11), r12 = await serverRow(app12);
  check("a Google Sheets copy pastes into the cells", r11.target_name === "Ann Lee" && r11.target_role === "Recruiter" && r12.target_role === "Hiring Manager", S([r11.target_name, r11.target_role, r12.target_role]));
  check("a quoted multi-line cell pastes as one cell", r12.target_name === 'Bo "B" Chan\nsecond line', S(r12.target_name));
  await clickCell(13, tn); await clickCell(15, tn, { mods: SHIFT });
  await paste("Same");
  await settle();
  const filled = await js(`return [13, 14, 15].map((i) => T19.cell(i, ${tn}).textContent)`);
  check("one value pastes into the whole range", S(filled) === S(["Same", "Same", "Same"]), S(filled));
  await press("Delete");
  await settle();
  const app13 = await appAt(13);
  check("Delete clears the range", !(await serverRow(app13)).target_name && (await cellText(15, tn)) === "", (await serverRow(app13)).target_name);
  await press("z", META);
  await settle();
  check("⌘Z undoes the clear", (await serverRow(app13)).target_name === "Same", (await serverRow(app13)).target_name);
  await press("z", META | SHIFT);
  await settle();
  check("⌘⇧Z redoes it", !(await serverRow(app13)).target_name, (await serverRow(app13)).target_name);
  await clickCell(11, tn);
  const cut = await copyEvent("cut");
  await settle();
  check("⌘X cuts", cut === "Ann Lee" && !(await serverRow(app11)).target_name, S([cut, (await serverRow(app11)).target_name]));
  await clickCell(16, da);
  await press("1"); await type("0/01/2026"); await press("Enter");
  await settle();
  await clickCell(16, da);
  const fillTo = await at(19, da);                                     // may scroll: measure the handle after
  const fh = await js("const el = document.querySelector('.g-fh'); const r = el.getBoundingClientRect(); return { x: Math.round(r.left + 3), y: Math.round(r.top + 3), hidden: el.hidden }");
  await drag(fh, fillTo);
  await settle();
  const dates = await js(`return [16, 17, 18, 19].map((i) => T19.cell(i, ${da}).textContent)`);
  check("the fill handle extends dates", S(dates) === S(["10/01/2026", "10/02/2026", "10/03/2026", "10/04/2026"]), S([dates, fh, await cur(), await js("return document.elementFromPoint(" + fh.x + "," + fh.y + ")?.className")]));
  const app19 = await appAt(19);
  await focusGrid();
  await press("z", META);
  await settle();
  check("⌘Z undoes the fill", (await serverRow(app19)).date_applied !== "10/04/2026", (await serverRow(app19)).date_applied);
  await clickCell(21, tn);
  await press("P"); await type("lain"); await press("Enter");
  await settle();
  await clickCell(21, tn);
  const plainTo = await at(23, tn);
  const plainFh = await js("const el = document.querySelector('.g-fh'); const r = el.getBoundingClientRect(); return { x: Math.round(r.left + 3), y: Math.round(r.top + 3) }");
  await drag(plainFh, plainTo);
  await settle();
  const plain = await js(`return [21, 22, 23].map((i) => T19.cell(i, ${tn}).textContent)`);
  check("the fill handle copies a plain value", S(plain) === S(["Plain", "Plain", "Plain"]), S(plain));
  const app23 = await appAt(23);
  check("the copied value is saved", (await serverRow(app23)).target_name === "Plain", (await serverRow(app23)).target_name);
  await clickCell(20, tn);
  await js("await navigator.clipboard.writeText('From the clipboard'); return true");
  await focusGrid();
  await press("v", META, ["paste"]);
  await settle();
  const app20 = await appAt(20);
  check("a real ⌘V pastes", (await serverRow(app20)).target_name === "From the clipboard", (await serverRow(app20)).target_name);

  // ---- menus ----
  await js("const w = document.querySelector('.g-wrap'); w.scrollTop = 0; w.scrollLeft = 0; await new Promise((r) => setTimeout(r, 300)); return true");
  await menuOf("Company Name"); await pick("Sort A → Z");
  const az = await cellText(0, 0);
  check("Sort A → Z", az === "Company 0", az);
  await menuOf("Company Name"); await pick("Sort Z → A");
  const za = await cellText(0, 0);
  check("Sort Z → A", za === "Company 999", za);
  const sLine = await stateText();
  await stateBtn("×");
  await sleep(300);
  check("the sort is shown and clearable", sLine.includes("Sorted by Company Name Z → A") && !(await stateText()).includes("Sorted"), sLine);
  await menuOf("Status(A/R)"); await pick("Filter by values");
  await sleep(300);
  await js(`for (const b of document.querySelectorAll('#pop .g-boxes input')) b.checked = b.value === 'Applied';
    [...document.querySelectorAll('#pop .btn')].find((b) => b.textContent === 'OK').click(); return true`);
  await sleep(450);
  const vals = await js("const k = T19.col('Status(A/R)'); const rows = [...document.querySelectorAll('.g tbody tr[data-app]')]; return { n: rows.length, all: rows.every((tr) => tr.children[k + 1].textContent === 'Applied') }");
  check("Filter by values", vals.all && vals.n > 10, S(vals));
  const fLine = await stateText();
  await stateBtn("Clear filters");
  await sleep(450);
  check("the filter is shown and clearable", fLine.includes("Filtered: Status(A/R)") && (await drawn()) > 20, fLine);
  await menuOf("Company Name"); await pick("Filter by condition");
  await sleep(300);
  await js(`document.querySelector('#pop select').value = 'is'; document.querySelector('#pop input[type=text]').value = 'company 1150';
    [...document.querySelectorAll('#pop .btn')].find((b) => b.textContent === 'OK').click(); return true`);
  await sleep(450);
  const cond = await js("return [...document.querySelectorAll('.g tbody tr[data-app]')].map((tr) => tr.children[1].textContent)");
  check("Filter by condition", S(cond) === S(["Company 1150"]), S(cond));
  await stateBtn("Clear filters");
  await sleep(450);
  await menuOf("Gemini chat number"); await pick("Hide column");
  check("Hide column", (await col("Gemini chat number")) === -1, await col("Gemini chat number"));
  await clickCell(0, 0);
  await press("f", META);
  await type("Company 1199");
  await sleep(300);
  const found = await js("return [document.querySelector('#g-find-n').textContent, T19.cur()?.text]");
  check("⌘F finds", found[0] === "1 of 1" && found[1] === "Company 1199", S(found));
  await js("const q = document.querySelector('#g-find-q'); q.value = 'Company 119'; q.dispatchEvent(new Event('input')); return true");
  await sleep(250);
  await press("Enter");
  const next = await js("return [document.querySelector('#g-find-n').textContent, T19.cur()?.text]");
  check("Enter goes to the next match", next[0].startsWith("2 of") && next[1].startsWith("Company 119"), S(next));
  await press("Enter", SHIFT);
  const prev = await js("return [document.querySelector('#g-find-n').textContent, T19.cur()?.text]");
  check("Shift+Enter goes to the previous match", prev[0].startsWith("1 of") && prev[1].startsWith("Company 119"), S(prev));
  await press("Escape");
  check("Esc closes find", !(await js("return !!document.querySelector('#g-find-q')")) && (await js("return document.activeElement.classList.contains('g-wrap')")));
  await js("const w = document.querySelector('.g-wrap'); w.scrollTop = 0; await new Promise((r) => setTimeout(r, 300)); return true");
  const before = await js("return [T19.cell(0, 0).textContent, T19.cell(1, 0).textContent]");
  const rc = await at(0, 0);
  await mouse(rc.x, rc.y, { button: "right" });
  await pick("Insert row below");
  await sleep(600);
  const ins = await js("return [T19.cell(0, 0).textContent, T19.cell(1, 0).textContent, T19.cell(2, 0).textContent, T19.cur()]");
  check("right-click → Insert row below", ins[0] === before[0] && ins[1] === "" && ins[2] === before[1] && ins[3]?.i === 1, S(ins));
  await focusGrid();
  await press("z", META);
  await sleep(700);
  const gone = ins[3] && await serverRow(ins[3].app);
  check("⌘Z removes the inserted row", (await cellText(1, 0)) === before[1] && !!gone?.error, S(gone));
  const above = [await cellText(0, 0), await cellText(1, 0)];
  const ra = await at(1, 0);
  await mouse(ra.x, ra.y, { button: "right" });
  await pick("Insert row above");
  await sleep(600);
  const insA = await js("return [T19.cell(0, 0).textContent, T19.cell(1, 0).textContent, T19.cell(2, 0).textContent, T19.cur()]");
  check("right-click → Insert row above", insA[0] === above[0] && insA[1] === "" && insA[2] === above[1] && insA[3]?.i === 1, S(insA));
  await focusGrid();
  await press("z", META);
  await sleep(700);
  const goneA = insA[3] && await serverRow(insA[3].app);
  check("⌘Z removes the row inserted above", (await cellText(1, 0)) === above[1] && !!goneA?.error, S([await cellText(1, 0), goneA]));
  const victim = { name: await cellText(1, 0), app: await appAt(1) };
  const vc = await at(1, 0);
  await mouse(vc.x, vc.y, { button: "right" });
  await pick("Delete row");
  const asked = await js("return document.querySelector('#ask[open]') ? document.querySelector('#ask-h').textContent : null");
  check("right-click → Delete row asks first", !!asked && asked.includes(victim.name), asked);
  await js("document.querySelector('#ask-ok').click(); return true");
  await sleep(900);
  check("the row is deleted", (await cellText(1, 0)) !== victim.name && !!(await serverRow(victim.app)).error, await cellText(1, 0));
  await js("[...document.querySelectorAll('#toasts .undo')].at(-1)?.click(); return true");
  await sleep(1000);
  check("Undo puts the row back", (await serverRow(victim.app)).company === victim.name
    && (await js(`return [...document.querySelectorAll('.g tbody tr[data-app]')].some((tr) => tr.dataset.app === '${victim.app}')`)), victim.name);
  await mouse(rc.x, rc.y, { button: "right" });
  await pick("Copy link to the job");
  await sleep(400);
  const clip = await js("return navigator.clipboard.readText()");
  check("Copy link to the job", clip.startsWith("https://jobs.example.com/"), clip);
  await mouse(rc.x, rc.y, { button: "right" });
  await pick("Open JD");
  check("Open JD", ((await js("return window.__opened.at(-1)")) || "").includes("/jd?"), await js("return window.__opened.at(-1)"));
  await menuOf("Company Name"); await pick("Filter by condition");
  await sleep(300);
  await js(`document.querySelector('#pop select').value = 'is'; document.querySelector('#pop input[type=text]').value = 'company 3';
    [...document.querySelectorAll('#pop .btn')].find((b) => b.textContent === 'OK').click(); return true`);
  await sleep(450);
  const p3 = await at(0, 0);
  await mouse(p3.x, p3.y, { button: "right" });
  await pick("Open resume");
  await sleep(700);
  check("Open resume", (await js("return window.__opened.at(-1)")) === "https://drive.google.com/file/d/f1/view", await js("return JSON.stringify(window.__opened)"));
  await stateBtn("Clear filters");
  await sleep(400);
  const grip = await js("const th = document.querySelector('.g thead th[data-j=\"3\"]'); const g = th.querySelector('.g-rz').getBoundingClientRect(); return { x: Math.round(g.left + 3), y: Math.round(g.top + 10), w: th.getBoundingClientRect().width }");
  await drag(grip, { x: grip.x + 60, y: grip.y });
  await openTracker("alex");
  const w2 = await js("return document.querySelector('.g thead th[data-j=\"3\"]').getBoundingClientRect().width");
  check("a resized column is remembered", Math.abs(w2 - (grip.w + 60)) <= 2, S([grip.w, w2]));
  await js("document.querySelector('#g-state input[type=checkbox]').click(); return true");
  await sleep(600);
  const heads = await js("return [...document.querySelectorAll('.g tr.g-day')].map((t) => t.textContent)");
  check("Group by day shows day headers", heads.length > 0 && /\(\d+\)$/.test(heads[0]), S(heads.slice(0, 3)));
  await js("document.querySelector('#g-state input[type=checkbox]').click(); return true");
  await sleep(400);

  // ---- Jordan ----
  await openTracker("jordan");
  const hers = await js("return [...document.querySelectorAll('.g thead th[data-j] .g-hl')].map((h) => h.firstChild.textContent)");
  const addrName = hers.includes("Addresss") ? "Addresss" : "Address";
  check("Jordan: the grid shows her columns", hers.includes(addrName) && hers[0] === "Company Name", S(hers.slice(0, 8)));
  const addr = hers.indexOf(addrName);
  await clickCell(0, addr);
  await press("N"); await type("ew City"); await press("Enter");
  await settle();
  const sApp = await appAt(0);
  check("Jordan: a cell edit is saved", (await serverRow(sApp)).address === "New City", S([await serverRow(sApp), await cellText(0, addr), await cellText(0, 0), await cur(), addr, sApp, await js("return [...document.querySelectorAll('.g tr[data-app]')].slice(0, 4).map((t) => t.dataset.app + ':' + t.children[1].textContent)"), await js("return location.hash + ' ' + document.querySelector('#who')?.textContent")]));

  // ---- phone ----
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 3, mobile: true });
  await openTracker("alex");
  await sleep(600);
  const ph = await js("return { grid: !!document.querySelector('.g'), cards: document.querySelectorAll('.t-card').length }");
  check("phone: the card list, no grid", !ph.grid && ph.cards > 10, S(ph));
  await js("document.querySelector('.t-card .tc-open').click(); return true");
  await sleep(1500);
  const opened = await js("return !!document.querySelector('.re-form, #detail .rowedit, [aria-label*=\"row\" i] form, .panel-open') || document.body.classList.contains('panel-open') || !!document.querySelector('#detail:not([hidden]) input')");
  check("phone: a tap opens the row editor", opened, await js("return document.body.className"));
  check("no page errors", errors.length === 0, S(errors));
} catch (err) {
  checks.push({ name: "runner", ok: false, detail: String(err?.stack || err) });
} finally {
  console.log(JSON.stringify({ checks }));
  close();
  process.exit(0);
}

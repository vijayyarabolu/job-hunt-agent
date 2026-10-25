// GOAL9 area A (top bar, tabs, filters) in headless Chrome against a throwaway DB (tests/test_goal9_a.py):
// every filter menu applies on one real click / tap (first visible change < 150 ms, the list equals the server's
// answer, the chip shows the choice, focus stays), single menus close on pick, More stays open with live ticks,
// Esc / outside click / scroll / another menu close without undoing, the phone sort segment, the drift banner
// dismiss, tabs, profile menu, theme, ⌘K, and the §6 hover labels (walk + hover + keyboard + long press).
// usage: node goal9_a.mjs <base url> <token> <chrome path> <profile dir>
import { sleep, start } from "./cdp.mjs";
import { hoverTip, missingTips } from "./tips.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR] = process.argv.slice(2);
const { send, js, until, key, errors, checks, check, server, close } = await start(CHROME, PROFILE_DIR);
const P = "tester";
const FAST_MS = 150;
const SINGLE = [["Role", "Data Engineer", { family: "data_engineer" }], ["Location", "TX", { state: "TX" }],
  ["Workplace", "Remote", { workplace: "remote" }], ["Match", "90%+", { min_match: "90" }], ["Posted pay", "$150k+", { salary: "150000" }]];
let touch = false;
let loads = 0;
// JH_SHOTS=<dir>: also save evidence screenshots there (off in normal test runs)
const SHOTS = process.env.JH_SHOTS;
const shot = async (name) => { if (!SHOTS) return;
  const { data } = await send("Page.captureScreenshot", { format: "png" });
  (await import("node:fs")).writeFileSync(`${SHOTS}/${name}.png`, Buffer.from(data, "base64")); };
const slug = (t) => t.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/-$/, "");

const rows = () => js("return document.querySelectorAll('#list .row[data-i]').length");
const popHidden = () => js("return document.querySelector('#pop').hidden");
const chips = () => js("return [...document.querySelectorAll('#fchips .chip.on')].map((c) => c.firstChild.textContent)");
const headText = () => js("return document.querySelector('#list-head h1')?.textContent || ''");
const focused = () => js("return (document.activeElement?.textContent || '').trim()");
const total = async (params) => (await server(`/api/jobs?${new URLSearchParams({ profile: P, tab: "recommended", ...params })}`)).total;
const settled = () => until("!document.querySelector('#list[aria-busy]') && window.__m?.list", 8000);
const feedReady = () => until("document.querySelectorAll('#list .row[data-i]').length >= 1 && !document.querySelector('#list[aria-busy]')", 10000);
const LOG = `window.__ev = []; const add = (t) => window.__ev.push(t.slice(0, 40));
  for (const ev of ['scroll', 'resize', 'pointerdown', 'focusin', 'click', 'touchend']) document.addEventListener(ev, (e) => add(ev + ':' + (e.target.id || e.target.className || e.target.nodeName)), true);
  new MutationObserver(() => add('pop:' + (document.querySelector('#pop').hidden ? 'closed' : 'open'))).observe(document.querySelector('#pop'), { attributes: true, attributeFilter: ['hidden'] }); return true`;
const load = async (hash) => {
  await send("Page.navigate", { url: `${BASE}/?n=${++loads}#${hash}` });
  await feedReady();
  await until("document.getElementById('tip')", 5000);          // tip.js loads after the first paint
  await js(LOG);
};
// the element's centre; a control outside the viewport (the phone's chip strip) is scrolled into view first, and
// window.__under remembers what sits at that point so a failed check can name it
const rect = (sel, text) => js(`const el = [...document.querySelectorAll(${JSON.stringify(sel)})].find((x) => x.offsetParent !== null
  && (${JSON.stringify(text)} === "" || x.textContent.trim().startsWith(${JSON.stringify(text)})));
  if (!el) return null; let r = el.getBoundingClientRect();
  if (r.left < 0 || r.right > innerWidth || r.top < 0 || r.bottom > innerHeight) { el.scrollIntoView({ block: "nearest", inline: "nearest" }); r = el.getBoundingClientRect(); }
  const p = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  window.__under = (document.elementFromPoint(p.x, p.y)?.outerHTML || "nothing").slice(0, 90); return p`);
const under = () => js("return window.__under");
// a real pointer press / tap, timed from the release (the click fires on it); window.__t0 is read by the marks
async function press(sel, text) {
  const r = await rect(sel, text);
  if (!r) return false;
  if (touch) {
    await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: r.x, y: r.y }] });
    await sleep(50);
    await js("window.__t0 = performance.now()");
    await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  } else {
    await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: r.x, y: r.y });
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: r.x, y: r.y, button: "left", clickCount: 1 });
    await sleep(40);
    await js("window.__t0 = performance.now()");
    await send("Input.dispatchMouseEvent", { type: "mouseReleased", x: r.x, y: r.y, button: "left", clickCount: 1 });
  }
  return true;
}
// marks: ms from the release to the first change of the list / chips / popover / head; heads: every head text seen
const arm = () => js(`window.__m = {}; window.__t0 = 1e12; window.__heads = [];
  const mark = (k) => { if (!window.__m[k]) window.__m[k] = Math.round(performance.now() - window.__t0); };
  const ob = (sel, k) => new MutationObserver(() => mark(k)).observe(document.querySelector(sel), { childList: true, subtree: true, attributes: true, characterData: true });
  ob('#list', 'list'); ob('#fchips', 'chips'); ob('#pop', 'pop'); ob('#sort', 'sort');
  new MutationObserver(() => { mark('head'); window.__heads.push(document.querySelector('#lh-title')?.textContent); }).observe(document.querySelector('#list-head'), { childList: true, subtree: true, characterData: true });
  return true`);
const marks = () => js("return window.__m");
const first = (m) => Math.min(...["list", "chips", "pop", "sort", "head"].map((k) => m[k] ?? 1e9));
const openMenu = async (chip) => { await press("#fchips .chip, #fchips .chip-body", chip); return until("!document.querySelector('#pop').hidden", 2000); };
// a press outside any menu: the list head on the desktop; on the phone the popover may cover it and the header is
// one big brand link, so the first inert point (list area, head, banner) that is not under the popover
const INERT = "#list-wrap, #list-more, #foot, #pane-list, .filters, #fchips, #list-head, #list-head *, .banners, #banner";
async function outside() {
  if (!touch) {                                           // a point of the list head that no menu covers
    const p = await js(`const h = document.querySelector("#list-head").getBoundingClientRect();
      for (let x = h.right - 12; x > h.left; x -= 24) for (let y = h.top + 8; y < h.bottom; y += 12) { const el = document.elementFromPoint(x, y);
        if (el && el.matches(${JSON.stringify(INERT)}) && !el.closest("#pop, button, a, .row")) { window.__under = el.outerHTML.slice(0, 90); return { x, y }; } } return null`);
    if (!p) return false;
    await send("Input.dispatchMouseEvent", { type: "mousePressed", x: p.x, y: p.y, button: "left", clickCount: 1 });
    return send("Input.dispatchMouseEvent", { type: "mouseReleased", x: p.x, y: p.y, button: "left", clickCount: 1 });
  }
  const p = await js(`for (let y = 60; y < 780; y += 24) for (const x of [30, 195, 360]) { const el = document.elementFromPoint(x, y);
    if (el && el.matches(${JSON.stringify(INERT)}) && !el.closest("#pop, button, a, .row, .toast, dialog")) { window.__under = el.outerHTML.slice(0, 90); return { x, y }; } } return null`);
  if (!p) return false;
  await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [p] });
  await sleep(50);
  await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  return true;
}
// a tab switch runs a View Transition: Chrome hit-tests <html> until it ends (~300 ms), so wait for it (open item A-8)
const switched = async () => { await feedReady(); await until("document.elementFromPoint(innerWidth / 2, 10) !== document.documentElement", 3000); };
const pressed = (sel) => js(`return document.querySelector(${JSON.stringify(sel)})?.getAttribute('aria-pressed') === 'true'`);

async function pickSingle(label, option, params, tag) {
  const before = await rows();
  check(`${tag}${label}: the menu opens on one click`, await openMenu(label), `events ${JSON.stringify(await js("return (window.__ev || []).slice(-12)"))}, tapped ${await under()}`);
  await arm();
  check(`${tag}${label}: the option is there`, await press("#pop .opt", option));
  await until("window.__m.list", 2000);
  const m = await marks();
  check(`${tag}${label}: one click applies`, first(m) <= FAST_MS && m.chips <= FAST_MS && m.pop <= FAST_MS, JSON.stringify(m));
  check(`${tag}${label}: the menu closes on pick`, await popHidden());
  check(`${tag}${label}: the chip shows the choice`, (await chips()).includes(`${label}: ${option}`), await chips());
  await settled();
  await shot(`${slug(tag || "desktop")}-${slug(label)}-picked`);
  const n = await rows();
  const want = await total(params);
  check(`${tag}${label}: the list matches the server`, n === want && n !== before, `${n} rows, server ${want}, before ${before}`);
  check(`${tag}${label}: the head counts it`, (await headText()).includes(`${want} `), await headText());
  const heads = await js("return window.__heads");
  const from = heads.findIndex((t) => t.includes("updating"));   // other head repaints (rg.js's button) may come first
  check(`${tag}${label}: the head never shows a stale count`, from >= 0 && heads.slice(from).every((t) => t.includes("updating") || t.includes(`${want} `)),
    JSON.stringify(heads));
  if (!touch) check(`${tag}${label}: focus stays on the chip`, (await focused()) === `${label}: ${option}`, await focused());
  await arm();
  await press("#fchips .chip-x", "");
  await settled();
  check(`${tag}${label}: × removes it`, (await rows()) === before && !(await chips()).length, `${await rows()} rows`);
}

async function segments(tag) {
  const before = await rows();
  await arm();
  await press("#fchips .seg button", "24h");
  await until("window.__m.list", 2000);
  let m = await marks();
  check(`${tag}Posted 24h: one click applies`, first(m) <= FAST_MS, JSON.stringify(m));
  await settled();
  check(`${tag}Posted 24h: the list matches the server`, (await rows()) === (await total({ posted: "24h" })) && (await rows()) !== before, `${await rows()} rows`);
  check(`${tag}Posted 24h: the button is pressed`, await pressed('#fchips .seg [data-v="24h"]'));
  if (!touch) check(`${tag}Posted 24h: focus stays on the button`, (await focused()) === "24h", await focused());
  await arm();
  await press("#fchips .seg button", "24h");
  await settled();
  check(`${tag}Posted 24h: a second click clears it`, (await rows()) === before);
  await arm();
  await press("#sort button", "Best match");
  await until("window.__m.list", 2000);
  m = await marks();
  check(`${tag}Best match: one click applies`, first(m) <= FAST_MS, JSON.stringify(m));
  await settled();
  check(`${tag}Best match: the head says so`, (await headText()).includes("best match first"), await headText());
  check(`${tag}Best match: the button is pressed`, await pressed("#sort [data-v=match]"));
  await arm();
  await press("#sort button", "Newest");
  await settled();
  check(`${tag}Newest: one click applies`, (await headText()).includes("newest first"), await headText());
}

async function more(tag) {
  const before = await rows();
  check(`${tag}More: the menu opens`, await openMenu("More"));
  await arm();
  await press("#pop .opt", "Big company");
  await until("window.__m.list", 2000);
  const m = await marks();
  check(`${tag}More: one click applies`, first(m) <= FAST_MS, JSON.stringify(m));
  check(`${tag}More: stays open with the tick`, !(await popHidden()) && await pressed("#pop [data-v=big]"));
  await shot(`${slug(tag || "desktop")}-more-open-ticked`);
  check(`${tag}More: the chip shows the choice`, (await chips()).includes("Company size: Big company"), await chips());
  await settled();
  check(`${tag}More: the list matches the server`, (await rows()) === (await total({ size: "big" })) && (await rows()) !== before, `${await rows()} rows`);
  await arm();
  await js("window.__log = []; for (const ev of ['pointerdown', 'focusin', 'scroll', 'resize']) document.addEventListener(ev, (e) => window.__log.push(`${ev}:${e.target.id || e.target.className || e.target.nodeName}`), true); return true");
  await press("#pop .opt", "Show too senior");
  await settled();
  check(`${tag}More: a second tick updates live`, !(await popHidden()) && (await rows()) === (await total({ size: "big", senior: "1" }))
    && await pressed("#pop [data-k=senior]"), `${await rows()} rows, pop hidden ${await popHidden()}, events ${JSON.stringify(await js("return window.__log"))}`);
  check(`${tag}More: the More chip stays its anchor`, await js("return document.querySelector('#fchips .chip[title^=\"More\"]').getAttribute('aria-expanded') === 'true'"));
  await key("Escape");
  check(`${tag}More: Esc closes without undoing`, (await popHidden()) && (await chips()).join() === "Company size: Big company,Show too senior", await chips());
  await openMenu("More");
  check(`${tag}More: it reopens with the ticks`, await js("return document.querySelectorAll('#pop [aria-pressed=true]').length === 4"),
    await js("return [...document.querySelectorAll('#pop [aria-pressed=true]')].map((o) => o.textContent)"));
  await outside();
  check(`${tag}More: an outside click closes without undoing`, (await popHidden()) && (await chips()).length === 2,
    `pop hidden ${await popHidden()}, chips ${await chips()}, tapped ${await under()}`);
  await openMenu("More");
  await press("#fchips .chip, #fchips .chip-body", "More");
  check(`${tag}More: clicking the chip again closes it`, await popHidden(), `tapped ${await under()}`);
  await arm();
  await press("#fchips .chip", "Clear all");
  await settled();
  check(`${tag}More: Clear all empties it`, (await rows()) === before && !(await chips()).length);
}

async function closeRules(tag) {
  await openMenu("Role");
  await key("Escape");
  check(`${tag}Role: Esc closes without undoing`, (await popHidden()) && !(await chips()).length);
  await openMenu("Role");
  await outside();
  check(`${tag}Role: an outside click closes without undoing`, (await popHidden()) && !(await chips()).length);
  await openMenu("Role");
  await js("document.querySelector('#list-wrap').dispatchEvent(new Event('scroll')); return true");
  check(`${tag}Role: a list scroll leaves it open`, !(await popHidden()));
  await js("document.querySelector('#fchips').dispatchEvent(new Event('scroll')); return true");
  check(`${tag}Role: a scroll event that does not move the chip leaves it open`, !(await popHidden()));
  if (touch) {             // the phone's chip strip scrolls sideways: really move the chip away
    await js("const f = document.querySelector('#fchips'); f.scrollLeft = f.scrollLeft + 120; return true");
    await sleep(150);
    check(`${tag}Role: a scroll that moves the chip closes the menu`, await popHidden(), await js("return document.querySelector('#fchips').scrollLeft"));
  } else await key("Escape");   // the desktop strip wraps: nothing can scroll a chip away there
  await openMenu("Role");
  await openMenu("Location");
  check(`${tag}opening another menu closes the first`, await js("return !document.querySelector('#pop').hidden && document.querySelector('#pop').getAttribute('aria-label') === 'Location'"));
  await key("Escape");
}

async function tips(tag, scopes) {
  for (const scope of scopes) {
    const missing = await missingTips(js, scope);
    check(`${tag}every control in ${scope} has a hover label`, missing.length === 0, missing.join(" | "));
  }
}

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:${P}` });
  await until("document.querySelectorAll('#list .row[data-i]').length >= 5", 10000);
  await until("document.getElementById('tip')", 5000);
  check("feed loads", (await rows()) === 5, await rows());
  await js(LOG);   // the input events and popover changes since load, so a menu that closes by itself names why

  // ---- §1 live controls, desktop ----
  for (const [label, option, params] of SINGLE) await pickSingle(label, option, params, "");
  await segments("");
  await more("");
  await closeRules("");
  const uid = (await server(`/api/jobs?profile=${P}&tab=recommended`)).jobs[0].uid;
  await js(`await fetch('/api/applied', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Jobhunt': '1' }, body: JSON.stringify({ profile: '${P}', uid: ${JSON.stringify(uid)} }) }); return true`);
  await openMenu("More");
  await arm();
  await press("#pop .opt", "Hide applied");
  await settled();
  check("Hide applied: the list matches the server", (await rows()) === 4 && (await rows()) === (await total({ hide_applied: "1" })), `${await rows()} rows`);
  check("Hide applied: the menu stays open with the tick", !(await popHidden()) && await pressed("#pop [data-k=hide_applied]"));
  await key("Escape");
  await arm();
  await press("#fchips .chip-x", "");
  await settled();
  // the strip never hides a chip behind the sort segment (three filters on at 1440)
  for (const [label, option] of SINGLE.slice(0, 3)) { await openMenu(label); await arm(); await press("#pop .opt", option); await settled(); }
  const hidden = await js(`const s = document.querySelector('#sort').getBoundingClientRect();
    return [...document.querySelectorAll('#fchips .chip, #fchips .seg')].filter((c) => { const r = c.getBoundingClientRect(); return r.right > innerWidth || r.right > s.left && r.top < s.bottom && r.bottom > s.top; }).map((c) => c.textContent.trim())`);
  check("no chip hides behind the sort segment", hidden.length === 0, hidden.join(", "));
  await arm();
  await press("#fchips .chip", "Clear all");
  await settled();

  // ---- tabs, profile menu, theme, ⌘K ----
  await js("document.querySelector('#list').focus(); return true");
  await key("2");
  check("tabs follow the number keys", await until("document.querySelector('#tabs [data-tab=saved]').getAttribute('aria-selected') === 'true'"));
  await js("document.querySelector('#tabs [data-tab=saved]').focus(); return true");
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowDown", windowsVirtualKeyCode: 40 });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowDown", windowsVirtualKeyCode: 40 });
  // the side menu runs top to bottom, Jobs first: Recommended, Saved, Archived, then Tailored resumes
  check("arrow keys move between tabs", await until("document.querySelector('#tabs [data-tab=archived]').getAttribute('aria-selected') === 'true' && document.activeElement?.dataset.tab === 'archived'"));
  await key("1");
  await switched();
  await js("document.querySelector('#who').click(); return true");   // the box itself (outside a name): the menu with Settings
  check("the profile menu opens", await until("!document.querySelector('#pop').hidden && document.querySelector('#pop [role=menu]')"), await under());
  await tips("", ["#pop"]);
  await key("Escape");
  check("Esc closes the profile menu", (await popHidden()) && (await focused()).length > 0);
  const theme = await js("return document.documentElement.dataset.theme || ''");
  await press("#theme", "");
  check("the theme toggles on one click", (await js("return document.documentElement.dataset.theme || ''")) !== theme);
  await press("#theme", "");
  await key("k", 2);
  check("⌘K opens the palette", await until("document.querySelector('#palette[open]') && document.querySelectorAll('#pal-list [role=option]').length > 3"));
  await tips("", ["#palette"]);
  await js("const i = document.querySelector('#pal-q'); i.value = 'sav'; i.dispatchEvent(new Event('input')); return true");
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", windowsVirtualKeyCode: 13, text: "\r", unmodifiedText: "\r" });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", windowsVirtualKeyCode: 13 });
  check("⌘K jumps to a tab", await until("!document.querySelector('#palette[open]') && document.querySelector('#tabs [data-tab=saved]').getAttribute('aria-selected') === 'true'"));
  await key("1");
  await switched();

  // ---- every top tab fits the bar (GOAL9 verifier: Follow-ups / Stats hid behind the search box at 1280 / 1024) ----
  for (const w of [1440, 1280, 1024, 800]) {
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: 900, deviceScaleFactor: 1, mobile: false });
    await sleep(150);
    check(`every top tab is in view at ${w} px`, await js(`const t = document.querySelector('#tabs'); const r = t.getBoundingClientRect();
      return t.scrollWidth <= t.clientWidth && [...t.querySelectorAll('button')].every((b) => b.getBoundingClientRect().right <= r.right + 0.5)`),
      await js("const t = document.querySelector('#tabs'); return `overflow ${t.scrollWidth - t.clientWidth} px`"));
  }
  check("the profile button keeps its name when the name is hidden", await js("return document.querySelector('#who').textContent.trim().length > 1"));
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await sleep(150);

  // ---- the drift notice (a line in the status popover, hidden "until it changes"; no banner) ----
  const statusTitle = () => js("return document.querySelector('#status').title");
  check("the drift notice shows in the status", await until("document.querySelector('#status').title.includes('Data drift')", 8000), await statusTitle());
  await js("document.querySelector('#status').click(); return 1");
  await until("[...document.querySelectorAll('#pop .lnk')].some((b) => b.textContent === 'Hide until it changes')", 3000);
  await press("#pop .lnk", "Hide until it changes");
  check("Hide until it changes hides it at once", await js("return !document.querySelector('#status').title.includes('drift') && ![...document.querySelectorAll('#pop .st-line')].some((l) => l.textContent.includes('Data drift'))"), await under());
  await key("Escape");
  await load(`recommended:${P}`);
  await sleep(2500);
  check("the dismissal is remembered", !(await statusTitle()).includes("Data drift"), await statusTitle());
  await js(`localStorage.setItem('jh.drift.${P}', 'Data drift: something older'); return true`);
  await load(`recommended:${P}`);
  check("it comes back when the drift changes", await until("document.querySelector('#status').title.includes('Data drift')", 8000), await statusTitle());

  // ---- §6 hover labels, desktop ----
  await tips("", [".top", "#filters", ".banners"]);
  for (const label of ["Role", "Location", "Workplace", "Match", "Posted pay", "Job board", "More"]) { await openMenu(label); await tips("", ["#pop"]); await key("Escape"); }
  let tip = await hoverTip(send, js, sleep, "#fchips .chip");
  check("hovering a chip shows its label", tip.text === "Role: filter by job family" && !tip.overlaps, JSON.stringify(tip));
  tip = await hoverTip(send, js, sleep, "#sort [data-v=match]");
  check("hovering Best match shows its label", tip.text === "Best match first" && !tip.overlaps, JSON.stringify(tip));
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 700, y: 600 });
  await js("document.querySelector('.skip').focus(); return true");
  for (let i = 0; i < 14 && !(await js("return document.activeElement?.classList.contains('chip')")); i++) await key("Tab");
  await sleep(450);
  check("keyboard focus on a chip shows its label", await js("const t = document.getElementById('tip'); return !t.hidden && t.textContent === document.activeElement.dataset.tip"),
    await js("return [document.activeElement?.textContent, document.getElementById('tip').hidden]"));
  check("no page errors (desktop)", errors.length === 0, errors.join(" | "));

  // ---- phone: 390 px, touch ----
  touch = true;
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await send("Emulation.setTouchEmulationEnabled", { enabled: true });
  await load(`recommended:${P}`);
  check("phone: the sort segment is in the strip", await js("const s = document.querySelector('#sort'); return s.offsetParent !== null && !!s.closest('#fchips')"));
  for (const [label, option, params] of SINGLE) await pickSingle(label, option, params, "phone ");
  await segments("phone ");
  await more("phone ");
  await closeRules("phone ");
  await tips("phone: ", [".top", "#filters", "#btabs"]);
  await press("#btabs [data-tab=more]", "");
  check("phone: the More tab menu opens", await until("!document.querySelector('#pop').hidden"));
  await shot("phone-more-tab-menu");
  await tips("phone: ", ["#pop"]);
  await outside();
  check("phone: a tap outside closes the More tab menu", await popHidden());
  await press("#btabs [data-tab=more]", "");
  await until("!document.querySelector('#pop').hidden");
  await key("Escape");
  check("phone: Esc closes the More tab menu", await popHidden());
  await press("#btabs [data-tab=more]", "");
  await until("!document.querySelector('#pop').hidden");
  await key("Escape");
  await press("#subtabs .chip", "Archived");     // GOAL21: a section's views are chips under the top bar (Jobs: Recommended · Saved · Archived)
  check("phone: picking a view in the section strip switches", (await popHidden()) && await until("location.hash.split('?')[0] === '#archived'"));
  await press("#btabs [data-tab=recommended]", "");
  await switched();
  const r = await rect("#fchips .chip", "Role");
  await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: r.x, y: r.y }] });
  await sleep(650);
  check("phone: a long press shows the label", await js("const t = document.getElementById('tip'); return !t.hidden && t.textContent === 'Role: filter by job family'"));
  await shot("phone-long-press-label");
  await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await sleep(100);
  check("phone: lifting the finger hides it", await js("return document.getElementById('tip').hidden"));
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || err);
} finally {
  console.log(JSON.stringify({ checks }));
  close();
}

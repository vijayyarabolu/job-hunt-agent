// GOAL37 in headless Chrome (tests/test_goal37_ui.py): the Tracker's search for a company shows the rows that name it
// and "N more where only the JD mentions it: show" (wide=1 in the address, kept by a reload, reset by new search
// text); the grid's column sort and filters are in the address; ⌘K lists applications first on the Tracker and the
// open jobs first on Recommended, and says "No open X jobs in your list right now" with the Company entry next.
// usage: node goal37_ui.mjs <base url> <token> <chrome path> <profile dir> [person id]
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, ME = "tester"] = process.argv.slice(2);
const { send, js, until, key, errors, checks, check, close } = await start(CHROME, PROFILE_DIR);
const S = JSON.stringify;

const hash = () => js("return decodeURIComponent(location.hash.replace(/\\+/g, ' '))");
const found = () => js("return document.querySelector('.t-found')?.textContent || ''");
const rows = () => js("return [...document.querySelectorAll('.g tbody tr[data-app]')].map((r) => r.innerText.replace(/\\s+/g, ' ').trim())");
const typeIn = (sel, text) => js(`const el = document.querySelector(${S(sel)}); el.focus(); el.value = ${S(text)};
  el.dispatchEvent(new Event('input', { bubbles: true })); return true`);
const jdToggle = () => js("const b = [...document.querySelectorAll('.t-found .lnk')].find((x) => /only the JD|found only in the JD/.test(x.textContent)); return b ? b.textContent : ''");
const clickToggle = () => js("[...document.querySelectorAll('.t-found .lnk')].find((x) => /only the JD|found only in the JD/.test(x.textContent))?.click(); return true");
const col = (name) => js(`return [...document.querySelectorAll('.g thead th[data-j]')].find((th) => th.querySelector('.g-hl')?.firstChild?.textContent === ${S(name)})?.dataset.j ?? null`);
const menuOf = async (name) => { const j = await col(name); await js(`document.querySelector('.g thead th[data-j="${j}"] .g-hm').click(); return true`); await sleep(250); };
const pick = async (text) => { await js(`[...document.querySelectorAll('#pop .opt')].find((o) => o.textContent.startsWith(${S(text)}))?.click(); return true`); await sleep(450); };
const stateText = () => js("return document.querySelector('#g-state')?.textContent || ''");
const loaded = () => until("document.querySelector('#who [data-person]') && document.querySelector('#t-search') && !document.querySelector('#view .placeholder')", 15000);
const palette = async (q) => {
  if (!(await js("return document.querySelector('#palette').open"))) { await js("document.querySelector('#cmdk')?.click(); return true"); await until("document.querySelector('#palette[open]')", 4000); }
  await typeIn("#pal-q", q);
  await until(`[...document.querySelectorAll('#pal-list [role=option] .pal-kind')].some((k) => /^(Job|Jobs|Company|Applied|Saved|Rejected|Tracker)$/.test(k.textContent.trim()))`, 8000);
  await sleep(300);
  return js("return [...document.querySelectorAll('#pal-list [role=option]')].map((li) => ({ kind: li.querySelector('.pal-kind').textContent.trim(), label: li.querySelector('.pal-label').textContent, icon: li.querySelector('.pal-kind use')?.getAttribute('href') || '' }))");
};
const closePalette = async () => { await key("Escape"); await until("!document.querySelector('#palette[open]')", 3000); };

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#tracker?p=${ME}` });
  check("the Tracker loads", await loaded(), await js("return document.body.innerText.slice(0, 300)"));
  await until("document.querySelectorAll('.g tbody tr[data-app]').length === 7", 10000);

  // ---- §1: a company's search, the JD-only rows one click away ----
  await typeIn("#t-search", "Amazon");
  await until("/3 found/.test(document.querySelector('.t-found')?.textContent || '')", 8000);
  const named = await rows();
  check("a company search shows the rows that name it", named.length === 3 && named.every((r) => /Amazon|Acme/.test(r)) &&
    !named.some((r) => /deCircle|USAA|JPMorgan/.test(r)), S(named));
  check("the found line offers the JD-only rows", (await jdToggle()) === "3 more where only the JD mentions it: show", await found());
  check("the address has the search, not wide", /[?&]q=Amazon(&|$)/.test(await hash()) && !/wide=1/.test(await hash()), await hash());
  await clickToggle();
  await until("document.querySelectorAll('.g tbody tr[data-app]').length === 6", 8000);
  const wide = await rows();
  check("show lists the JD-only rows too", wide.length === 6 && wide.some((r) => /deCircle/.test(r)) && wide.some((r) => /JPMorgan/.test(r)), S(wide));
  check("the toggle now hides them", (await jdToggle()) === "Hide the 3 found only in the JD", await found());
  check("the address keeps wide=1", /wide=1/.test(await hash()), await hash());
  await send("Page.reload");
  await loaded();
  check("a reload keeps the JD-only rows shown", (await until("document.querySelectorAll('.g tbody tr[data-app]').length === 6", 10000)) &&
    (await jdToggle()) === "Hide the 3 found only in the JD" && /wide=1/.test(await hash()), `${await found()} ${await hash()}`);
  await typeIn("#t-search", "Amazon Seattle");
  await until("/2 found/.test(document.querySelector('.t-found')?.textContent || '')", 8000);
  check("new search text starts without the JD-only rows", !/wide=1/.test(await hash()) && (await rows()).length === 2, `${await hash()} ${S(await rows())}`);
  await typeIn("#t-search", "Amazon");
  await until("/3 found/.test(document.querySelector('.t-found')?.textContent || '')", 8000);
  check("back to the company: the toggle says show again", (await jdToggle()) === "3 more where only the JD mentions it: show", await found());

  // ---- §2: the grid's column sort and filters are in the address ----
  await menuOf("Company Name"); await pick("Sort Z → A");
  check("a column sort goes into the address", /gsort=-s:Company Name/.test(await hash()), await hash());
  await send("Page.reload");
  await loaded();
  await until("document.querySelectorAll('.g tbody tr[data-app]').length === 3", 10000);
  const sortedRows = await rows();
  check("a reload keeps the column sort", (await stateText()).includes("Sorted by Company Name Z → A") && /^\d*\s*Amazon/.test(sortedRows[0]) && /Acme/.test(sortedRows[2]),
    `${await stateText()} ${S(sortedRows)}`);
  await menuOf("Company Name"); await pick("Filter by condition");
  await js("const p = document.querySelector('#pop'); const s = p.querySelector('select'); s.value = 'is'; s.dispatchEvent(new Event('change')); p.querySelector('input[type=text]').value = 'Acme'; [...p.querySelectorAll('button')].find((b) => b.textContent === 'OK').click(); return true");
  await until("document.querySelectorAll('.g tbody tr[data-app]').length === 1", 6000);
  check("a column filter goes into the address", /gf=\{"s:Company Name":\{"op":"is","text":"Acme"\}\}/.test(await hash()), await hash());
  await send("Page.reload");
  await loaded();
  check("a reload keeps the column filter", (await until("document.querySelectorAll('.g tbody tr[data-app]').length === 1", 10000)) &&
    (await stateText()).includes("Filtered: Company Name (1 of 3)"), await stateText());
  await js("[...document.querySelectorAll('#g-state .mini')].find((b) => b.textContent === 'Clear filters')?.click(); [...document.querySelectorAll('#g-state .mini')].find((b) => b.textContent === '×')?.click(); return true");
  await until("document.querySelectorAll('.g tbody tr[data-app]').length === 3", 6000);
  check("clearing them takes them out of the address", !/gsort|gf=/.test(await hash()), await hash());
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#tracker?p=${ME}&gf=${encodeURIComponent("{broken")}` });
  await loaded();
  check("a broken filter in a link is ignored", await until("document.querySelectorAll('.g tbody tr[data-app]').length === 7", 10000), await stateText());

  // ---- ⌘K: the Tracker lists applications first, Recommended the open jobs ----
  const onTracker = await palette("amazon");
  const firstJob = onTracker.findIndex((x) => x.kind === "Job"), firstApp = onTracker.findIndex((x) => x.kind === "Applied");
  check("⌘K on the Tracker: applications first", firstApp >= 0 && firstJob > firstApp && /^Amazon · /.test(onTracker[firstApp].label), S(onTracker.slice(0, 6)));
  await closePalette();
  await js("document.querySelector('#tabs [data-tab=recommended]').click(); return true");
  await until("document.querySelectorAll('#list .row[data-i]').length > 0", 10000);
  const onJobs = await palette("amazon");
  check("⌘K on Recommended: Amazon's open jobs first", onJobs[0]?.kind === "Job" && onJobs.slice(0, 2).every((x) => /^Amazon · /.test(x.label)) &&
    onJobs.findIndex((x) => x.kind === "Applied") > 1, S(onJobs.slice(0, 6)));
  check("⌘K: an AWS title at another company is not an Amazon job", !onJobs.some((x) => x.kind === "Job" && /AWS Machine Learning/.test(x.label)), S(onJobs));
  const none = await palette("globex");
  check("⌘K says when there is no open job at the company", none[0]?.label === "No open Globex jobs in your list right now" && none[0]?.kind === "Jobs" &&
    none[0]?.icon === "#i-ban", S(none.slice(0, 3)));
  check("the Company entry comes next", none[1]?.kind === "Company" && none[1]?.label === "Globex", S(none.slice(0, 3)));
  await closePalette();
  check("no page errors", !errors.length, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
} finally {
  console.log(JSON.stringify({ checks }));
  close();
  process.exit(0);
}

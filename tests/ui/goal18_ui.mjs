// GOAL18 §2 / §3 in headless Chrome against a throwaway DB (tests/test_goal18_ui.py): Settings (a page since GOAL21)
// closes by Back, Esc, the browser Back and the menu (desktop and phone); the Resumes tab's day headers, numbers, times, keys, phone width.
// usage: node goal18_ui.mjs <base url> <token> <chrome path> <profile dir> <expected json>
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXPECTED] = process.argv.slice(2);
const want = JSON.parse(EXPECTED);
const { send, js, until, key, errors, check, checks, clickText, close } = await start(CHROME, PROFILE_DIR);
const S = JSON.stringify;
const OPEN = "(document.body.dataset.view === 'settings' && document.querySelector('.set-page h1'))";   // a page, not a dialog
const idle = () => until("!document.querySelector('#list[aria-busy]')", 8000);
let loads = 0;
const load = async (hash) => {
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}&n=${++loads}#${hash}` });
  await until("document.body.classList.contains('rg-on')", 8000);
  await idle();
  await sleep(300);
};
const tabTo = async (tab) => { await js(`[...document.querySelectorAll('[data-tab=${tab}]')].find((b) => b.offsetParent)?.click(); return true`); await sleep(400); await idle(); await sleep(300); };
const size = (width, height, mobile = false) =>
  send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: mobile ? 3 : 1, mobile });
const openSettings = async () => {
  await js("document.querySelector('#who').click(); return true");
  await clickText("#pop .opt", "Settings");
  return until(`${OPEN}?.textContent === 'Settings'`, 6000);
};
const shut = () => until(`!${OPEN}`, 3000);

// Settings is a page; it closes by its Back button, Esc, the browser's Back and the side menu, and goes back
// to the tab it was opened from (it used to be a dialog with Cancel / Save / a click outside)
async function settings(name) {
  const ways = {
    "its Back button": () => clickText(".set-back", "← Back"),
    Esc: () => key("Escape"),
    Back: () => js("history.back(); return true"),
    "the menu": () => js(`[...document.querySelectorAll('[data-tab=saved], #subtabs [data-sub=saved]')].find((b) => b.offsetParent)?.click()
      || [...document.querySelectorAll('[data-tab=recommended]')].find((b) => b.offsetParent)?.click(); return true`),
  };
  for (const [how, act] of Object.entries(ways)) {
    const hash = await js("return location.hash.split(':')[0]");      // the tab (a person in the address is dropped on any switch)
    const opened = await openSettings();
    await act();
    const closed = opened && await shut();
    check(`${name}: Settings closes by ${how}`, closed, S({ opened, hash, now: await js("return location.hash") }));
    if (how !== "the menu") check(`${name}: closing Settings (${how}) goes back to the same tab`, (await js("return location.hash.split(':')[0]")) === hash, `${hash} -> ${await js("return location.hash")}`);
    if (!closed || how === "the menu") await load("recommended:tester");
    await sleep(300);
  }
}

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await size(1440, 900);
  await load("recommended:tester");
  await settings("desktop");
  // a field saves on its own (change / Enter) and says so; nothing is lost by leaving
  await openSettings();
  await js("[...document.querySelectorAll('.set-nav [data-sec]')].find((b) => b.dataset.sec === 'filters').click(); return true");
  await until("document.querySelector('.set-body input[type=number]')", 4000);
  await js(`const i = document.querySelector('.set-body input[type=number]'); i.value = '5'; i.dispatchEvent(new Event('change')); return true`);
  check("desktop: a changed field says Saved", await until("document.querySelector('.set-body .saved-state')?.textContent === 'Saved ✓'", 6000),
    await js("return document.querySelector('.set-body .saved-state')?.textContent"));
  await key("Escape");
  await shut();
  check("desktop: the field saved the years cap", (await js("return (await (await fetch('/api/settings?profile=tester')).json()).max_required_years")) === 5);

  // ---- §3 Resumes → Ready by day ----
  await load("recommended:tester");
  await tabTo("resumes");
  await js("[...document.querySelectorAll('.rq-seg button')].find((b) => b.textContent.startsWith('Ready')).click(); return true");
  await until("document.querySelectorAll('#list .row[data-i]').length === 5", 8000);
  await idle();
  const got = await js(`return {
    heads: [...document.querySelectorAll('#list > .section-head')].map((x) => x.textContent),
    order: [...document.querySelectorAll('#list > li')].map((x) => x.classList.contains('section-head') ? '#' : x.querySelector('.row-title').textContent),
    nums: [...document.querySelectorAll('#list .row[data-i] .rq-n')].map((x) => x.textContent) }`);
  check("Ready: one header per day, newest first, More than 30 days last", S(got.heads) === S(want.heads), S([got.heads, want.heads]));
  const rows = got.order.filter((x) => x !== "#");
  check("Ready: needs you first inside a day", S(rows) === S(want.rows), S([rows, want.rows]));
  check("Ready: rows numbered inside each day", S(got.nums.map((x) => parseInt(x, 10))) === S(want.numbers), S(got.nums));
  check("Ready: each row shows its time", want.times.every((t, i) => got.nums[i]?.includes(t)), S([got.nums, want.times]));
  await js("document.querySelector('#list').focus(); return true");
  const seen = [];
  for (let i = 0; i < 5; i++) {
    seen.push(await js("return document.querySelector('#list .row[aria-selected=true] .row-title')?.textContent"));
    await key("j");
  }
  check("j moves down the rows in the order shown", S(seen) === S(want.rows), S(seen));

  // ---- phone ----
  await size(390, 844, true);
  await load("recommended:tester");
  await tabTo("resumes");
  await until("document.querySelectorAll('#list .row[data-i]').length >= 1", 8000);
  const wide = await js("return { sw: document.scrollingElement.scrollWidth, w: innerWidth, head: document.querySelector('#list > .section-head')?.getBoundingClientRect().width }");
  check("phone: no sideways scroll on Resumes", wide.sw <= wide.w && wide.head > 0, S(wide));
  await load("recommended:tester");
  await settings("phone");
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
}
close();
console.log(JSON.stringify({ checks }));

// resumegen GOAL3 §7 (tests/test_goal3_reset_ui.py): a reload or a new open comes back to the default view, not the
// filters / sort / tab left last time: Recommended, no filters, Newest, the cursor on the first row, the list at the
// top. Only the person (and theme) is kept.
// usage: node goal3_reset.mjs <base url> <token> <chrome path> <profile dir>
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR] = process.argv.slice(2);
const { send, js, until, errors, checks, check, close } = await start(CHROME, PROFILE_DIR);
const rows = "document.querySelectorAll('#list .row[data-i]').length >= 1 && !document.querySelector('#list[aria-busy]')";
const tab = () => js("return document.querySelector('#tabs [aria-selected=true]')?.dataset.tab");
const view = () => js(`return JSON.stringify({ tab: document.querySelector('#tabs [aria-selected=true]')?.dataset.tab,
  hash: location.hash, chips: [...document.querySelectorAll('#fchips .chip.on')].map((c) => c.textContent),
  posted: document.querySelector('#fchips .seg [aria-pressed=true]')?.textContent || '',
  sort: document.querySelector('#sort [aria-pressed=true]')?.dataset.v, cur: document.querySelector('#list .row[aria-selected=true]')?.id,
  top: document.querySelector('#list-wrap')?.scrollTop, y: scrollY, who: document.querySelector('#who .who-name')?.textContent })`);
const isDefault = (v) => v.tab === "recommended" && v.hash.startsWith("#recommended") && !v.chips.length && v.posted === "Any" && v.sort === ""
  && v.cur === "job-0" && v.top === 0 && v.y === 0;
try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 420, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:other` });
  await until(rows, 10000);
  check("first open: the default view", isDefault(JSON.parse(await view())), await view());

  // change the filters, the sort, the selection, the scroll and the tab
  await js("[...document.querySelectorAll('#fchips .seg button')].find((b) => b.textContent === '3d').click()");
  await js("[...document.querySelectorAll('#fchips .chip:not(.on)')].find((c) => c.textContent.startsWith('Workplace')).click()");
  await until("!document.querySelector('#pop').hidden", 4000);
  await js("[...document.querySelectorAll('#pop .opt')].find((o) => o.textContent.includes('Remote')).click()");
  await js("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))");
  await js("document.querySelector('#sort [data-v=match]').click()");
  await until(rows, 8000);
  await js("const rs = document.querySelectorAll('#list .row[data-i]'); rs[rs.length - 1].querySelector('.row-title').click()");
  await js("document.querySelector('#list-wrap').scrollTop = 200");
  await sleep(300);
  const changed = JSON.parse(await view());
  check("the view was changed: filters, sort, selection", changed.chips.length >= 1 && changed.posted === "3d" && changed.sort === "match"
    && changed.cur !== "job-0", JSON.stringify(changed));
  await js("document.querySelector('#tabs [data-tab=saved]').click()");
  check("and the tab", await until("location.hash === '#saved'", 4000) && (await tab()) === "saved", await view());

  // a plain reload: the defaults again, same person
  await send("Page.reload");
  await sleep(300);
  await until(rows, 10000);
  await sleep(500);
  const after = JSON.parse(await view());
  check("a reload comes back to the default view", isDefault(after), JSON.stringify(after));
  check("a reload keeps the person", after.who === "Olive Other", after.who);
  check("nothing about the view is stored", await js("return !Object.keys(localStorage).some((k) => k.startsWith('jh.filters') || k === 'jh.tab')"),
    await js("return Object.keys(localStorage).join(' ')"));

  // a new open with an old address (#saved): Recommended
  await js("document.querySelector('#tabs [data-tab=saved]').click()");
  await until("location.hash === '#saved'", 4000);
  await send("Page.navigate", { url: `${BASE}/?n=2#saved` });
  await sleep(300);
  await until(rows, 10000);
  check("a new open starts on Recommended", JSON.parse(await view()).tab === "recommended", await view());

  // while the page is open, filters stay when moving between tabs
  await js("[...document.querySelectorAll('#fchips .seg button')].find((b) => b.textContent === '3d').click()");
  await until(rows, 8000);
  await js("document.querySelector('#tabs [data-tab=saved]').click()");
  await until("location.hash === '#saved'", 4000);
  await js("document.querySelector('#tabs [data-tab=recommended]').click()");
  await until(rows, 8000);
  check("while the page is open the filters stay", JSON.parse(await view()).posted === "3d", await view());
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, String(err?.stack || err));
} finally {
  console.log(JSON.stringify({ checks }));
  await close();
}

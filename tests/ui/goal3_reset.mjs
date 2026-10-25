// resumegen GOAL3 §7, GOAL33 §3d, GOAL35 §1 (tests/test_goal3_reset_ui.py): a reload stays on the page the user was on with
// its filters, sort and the job open there (GOAL35 replaced GOAL33's "reset to defaults"); the state is in the address,
// never in storage. A new open of "/" starts on Recommended with the defaults; a copied link opens its page.
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
  const openTitle = await js("return document.querySelector('#list .row[aria-selected=true] .row-title')?.textContent");

  // a reload on Recommended with a job open: the same page, job, filters and sort
  await send("Page.reload");
  await sleep(300);
  await until(rows, 10000);
  await sleep(800);
  const after = JSON.parse(await view());
  check("a reload stays on the page with its filters and sort", after.tab === "recommended" && after.hash.startsWith("#recommended")
    && after.chips.length >= 1 && after.posted === "3d" && after.sort === "match" && after.y === 0, JSON.stringify(after));
  check("a reload comes back to the job that was open", !!openTitle && await until(`document.querySelector('#detail h1, #detail h2')?.textContent.includes(${JSON.stringify(openTitle || "?")})`, 4000),
    `${openTitle} vs ${await js("return document.querySelector('#detail h1, #detail h2')?.textContent")}`);
  check("a reload keeps the person", after.who === "Olive Other", after.who);
  check("nothing about the view is stored", await js("return !Object.keys(localStorage).some((k) => k.startsWith('jh.filters') || k === 'jh.tab' || k.startsWith('jh.spons'))"),
    await js("return Object.keys(localStorage).join(' ')"));

  // another page: a reload stays there
  await js("document.querySelector('#tabs [data-tab=saved]').click()");
  check("and the tab", await until("location.hash.startsWith('#saved')", 4000) && (await tab()) === "saved", await view());
  await send("Page.reload");
  await sleep(300);
  await until("document.querySelector('#tabs [aria-selected=true]')", 10000);
  await sleep(800);
  check("a reload on Saved stays on Saved", (await tab()) === "saved" && (await js("return location.hash")).startsWith("#saved"), await view());

  // a copied link (#saved): Saved; a new open of "/": Recommended with the defaults
  await send("Page.navigate", { url: `${BASE}/?n=2#saved` });
  await sleep(300);
  await until("document.querySelector('#tabs [aria-selected=true]')", 10000);
  check("a copied link opens its page", (await tab()) === "saved", await view());
  await send("Page.navigate", { url: `${BASE}/?n=3` });
  await sleep(300);
  await until(rows, 10000);
  const fresh = JSON.parse(await view());
  check("a new open starts on Recommended", fresh.tab === "recommended" && !fresh.chips.length && fresh.posted === "Any" && fresh.sort === "", await view());

  // while the page is open, filters stay when moving between tabs
  await js("[...document.querySelectorAll('#fchips .seg button')].find((b) => b.textContent === '3d').click()");
  await until(rows, 8000);
  await js("document.querySelector('#tabs [data-tab=saved]').click()");
  await until("location.hash.startsWith('#saved')", 4000);
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

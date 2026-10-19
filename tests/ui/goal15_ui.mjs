// GOAL15 §1 checks in headless Chrome against a throwaway DB (tests/test_goal15_ui.py): the tracker still scrolls
// with "Updates from email" open (desktop, 1024, phone), the folded summary line, the kept open/closed choice, Cold
// as one grouped line (Show / Show more / Undo all) and today's emails 20 at a time.
// usage: node goal15_ui.mjs <base url> <token> <chrome path> <profile dir> <cold rows> <emails>
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, COLD, EMAILS] = process.argv.slice(2);
const { send, js, until, errors, checks, check, close } = await start(CHROME, PROFILE_DIR);
const S = JSON.stringify;
const PANEL = "document.querySelector('#view > .mail-box > details.mail')";
let loads = 0;
const load = async () => {
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}&n=${++loads}#recommended:tester` });
  for (let i = 0; i < 30 && !(await js(`return document.body.dataset.view === 'table' && !!${PANEL}`)); i++) {
    await js("[...document.querySelectorAll('[data-tab=tracker]')].find((b) => b.offsetParent)?.click(); return true");
    await sleep(400);                                // start-up may route back to its first tab: click until it holds
  }
  const ready = await until(`${PANEL}?.querySelector('summary') && document.querySelector('.g tbody tr[data-app], .t-cards > li')`, 10000);
  if (!ready) check(`load ${loads}`, false, await js("return location.href + ' ' + document.body.dataset.view + ' ' + document.querySelector('#view')?.innerHTML.slice(0, 600)"));
  await sleep(400);
};
const size = (width, height, mobile = false) =>
  send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: mobile ? 3 : 1, mobile });
const isOpen = () => js(`return ${PANEL}.open`);
const openPanel = async () => {
  if (!(await isOpen())) await js(`${PANEL}.querySelector('summary').click(); return true`);
  return until(`${PANEL}.open`);
};
const press = async (label, where = "") => {
  await js(`[...${PANEL}.querySelectorAll(${S(`${where} [data-mail]`)})].find((b) => b.textContent === ${S(label)}).click(); return true`);
  await sleep(500);
};
const count = (sel) => js(`return ${PANEL}.querySelectorAll(${S(sel)}).length`);

// the open panel, the table / cards below it, scrolled to the end: the last row is on screen and nothing covers it
async function layout(name) {
  await openPanel();
  await sleep(300);
  const got = await js(`try { const p = document.querySelector("#view > .mail-box").getBoundingClientRect(), box = document.querySelector('.g-wrap, .t-cards');
    const r = box.getBoundingClientRect();
    return { vh: innerHeight, panel: Math.round(p.height), panelBottom: Math.round(p.bottom), top: Math.round(r.top),
             bottom: Math.round(r.bottom), seen: Math.round(Math.min(r.bottom, innerHeight) - r.top) }; } catch (e) { return { err: String(e) }; }`);
  check(`${name}: the open panel stays within half the screen`, got.panel <= got.vh * 0.5 + 24, S(got));
  check(`${name}: the tracker table is on screen below the open panel`, got.top >= got.panelBottom - 1 && got.seen >= 140, S(got));
  const end = await js(`try { const box = document.querySelector('.g-wrap, .t-cards'); box.scrollTop = box.scrollHeight;
    await new Promise((r) => setTimeout(r, 400));
    const rows = box.querySelectorAll('tr[data-app], li'), last = rows[rows.length - 1], r = last.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + Math.min(40, r.width / 2), r.top + r.height / 2);
    return { scrolled: box.scrollTop, room: box.scrollHeight - box.clientHeight, text: last.textContent.slice(0, 40),
             onScreen: r.top >= 0 && r.bottom <= innerHeight + 1, reached: !!hit && last.contains(hit) }; } catch (e) { return { err: String(e) }; }`);
  check(`${name}: the tracker table scrolls to its last row`, end.scrolled > 0 && end.scrolled >= end.room - 2 && end.onScreen && end.reached, S(end));
}

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await size(1440, 900);
  await load();

  // ---- folded by default, one summary line ----
  check("the panel starts folded", (await isOpen()) === false);
  const line = await js(`return ${PANEL}.querySelector('summary').textContent`);
  check("the folded line sums up today", line === `Email today: ${EMAILS} received · ${COLD} went Cold`, line);

  // ---- scroll with the panel open: desktop ----
  await layout("1440x900");

  // ---- Cold: one grouped line ----
  const cold = await js(`const c = ${PANEL}.querySelectorAll('.cold'); return { n: c.length, text: c[0]?.textContent || '' }`);
  check("Cold is one line, no names until Show", cold.n === 1 && cold.text.includes(`${COLD} applications went Cold`)
    && cold.text.includes("no reply in 30 days") && (await count(".cold-names li")) === 0, S(cold));
  await press("Show", ".cold");
  check("Show lists 20 names", (await count(".cold-names li")) === 20, await count(".cold-names li"));
  await press("Show more", ".cold");
  check("Show more lists 20 more", (await count(".cold-names li")) === 40, await count(".cold-names li"));

  // ---- today's emails: newest first, 20 at a time ----
  const first = await js(`return [...${PANEL}.querySelectorAll('.fu-row a, .fu-row b')].map((x) => x.textContent).find((t) => t.startsWith('ZZ_TEST thanks')) || ''`);
  const rows = await count(".fu-row");
  check("today's emails: 20 newest first", rows === 20 && first === `ZZ_TEST thanks ${EMAILS - 1}`, `${rows} rows, first ${first}`);
  await js(`[...${PANEL}.querySelectorAll('[data-mail=view]')].find((b) => b.textContent === 'Show more' && !b.closest('.cold')).click(); return true`);
  await sleep(500);
  check("Show more shows the rest", (await count(".fu-row")) === Number(EMAILS), await count(".fu-row"));

  // ---- the open / closed choice is kept for the tab session ----
  await load();
  check("the open choice is kept on reload", await isOpen());
  await js(`${PANEL}.querySelector('summary').click(); return true`);
  await sleep(300);
  await load();
  check("the closed choice is kept on reload", (await isOpen()) === false);

  // ---- 1024 and a phone ----
  await size(1024, 768);
  await load();
  await layout("1024x768");
  await size(390, 844, true);
  await load();
  await layout("phone 390x844");

  // ---- one Undo all ----
  await size(1440, 900);
  await load();
  await openPanel();
  await press("Undo all", ".cold");
  const after = await js(`return ${PANEL}.querySelectorAll('.cold').length`);
  const toast = await js("return [...document.querySelectorAll('.toast')].map((t) => t.textContent).join(' | ')");
  check("Undo all puts every Cold row back", after === 0 && toast.includes(`${COLD} applications are back to Applied`), `${after} Cold lines; ${toast}`);
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
}
close();
console.log(JSON.stringify({ checks }));

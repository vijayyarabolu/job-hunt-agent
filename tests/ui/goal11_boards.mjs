// GOAL11 §8 (tests/test_goal11_boards.py): the "Job board" chip in headless Chrome against a throwaway DB: one option
// per board with the server's live count, several at once, independent of Posted ("Any" / "30d"), and with a board
// picked the list pages past the card cap ("Load more" loads the next pages by itself on scroll, no cap).
// usage: node goal11_boards.mjs <base url> <token> <chrome path> <profile dir>
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR] = process.argv.slice(2);
const { send, js, until, errors, checks, check, server, close } = await start(CHROME, PROFILE_DIR);
const P = "tester";
const rows = () => js("return document.querySelectorAll('#list .row[data-i]').length");
const reply = (params) => server(`/api/jobs?${new URLSearchParams({ profile: P, tab: "recommended", ...params })}`);
const settled = () => until("!document.querySelector('#list[aria-busy]') && document.querySelectorAll('#list .row[data-i]').length >= 1", 8000);
const pressedPosted = () => js("return document.querySelector('#fchips .seg [aria-pressed=true]')?.textContent || ''");
const openBoards = async () => {
  await js("[...document.querySelectorAll('#fchips .chip')].find((c) => c.textContent.startsWith('Job board')).click()");
  return until("!document.querySelector('#pop').hidden", 4000);
};
const pick = (label) => js(`[...document.querySelectorAll('#pop .opt')].find((o) => o.textContent.startsWith(${JSON.stringify(label)})).click()`);
const options = () => js("return [...document.querySelectorAll('#pop .opt')].map((o) => [o.textContent, o.classList.contains('muted')])");
const esc = () => js("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))");
const more = () => js("return [...document.querySelectorAll('#list-more button')].find((b) => b.textContent.startsWith('Load more'))?.textContent || ''");
const allOf = (n) => until(`document.querySelectorAll('#list .row[data-i]').length === ${n}`, 8000);

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:${P}` });
  await settled();
  check("Posted has Any (pressed: no limit) and 30d", (await pressedPosted()) === "Any"
    && await js("return [...document.querySelectorAll('#fchips .seg button')].some((b) => b.textContent === '30d')"), await pressedPosted());

  const all = await reply({});
  check("the chip opens with one option per board and the server's counts", await openBoards()
    && JSON.stringify((await options()).slice(1).map(([t]) => t)) === JSON.stringify(all.boards.map((b) => `${b.label} (${b.n})`)),
  JSON.stringify(await options()));
  check("a board with no jobs is greyed, not hidden", (await options()).some(([t, dim]) => t.startsWith("Ashby (0)") && dim),
    JSON.stringify(await options()));

  await pick("Workday");
  await sleep(200);
  check("a multi-select: the menu stays open after a pick", !(await js("return document.querySelector('#pop').hidden")));
  await pick("Lever");
  await esc();
  await settled();
  const both = await reply({ board: "lever,workday" });
  const chip = () => js("return document.querySelector('#fchips .chip.on')?.textContent || ''");
  check("two boards: the list is both boards' jobs", (await allOf(both.total)) && (await chip()).startsWith("Job board: Workday, Lever"),
    `${await rows()} rows, server ${both.total}; ${await chip()}`);

  await js("[...document.querySelectorAll('#fchips .seg button')].find((b) => b.textContent === '30d').click()");
  await settled();
  const month = await reply({ board: "lever,workday", posted: "30d" });
  check("with Posted 30d the board filter still holds", (await pressedPosted()) === "30d" && (await allOf(month.total)),
    `${await rows()} rows, server ${month.total}`);
  await js("[...document.querySelectorAll('#fchips .seg button')].find((b) => b.textContent === 'Any').click()");
  await settled();

  const lever = await reply({ board: "lever" });
  await openBoards();
  await pick("Workday");                              // Workday off again: Lever alone
  await esc();
  await settled();
  check("past the cap: the next pages load by themselves", lever.jobs.length < lever.total && (await allOf(lever.total)),
    `first page ${lever.jobs.length}, ${await rows()} rows of ${lever.total}`);
  check("Show more pages through every match of the board", (await rows()) === lever.total && !(await more()),
    `${await rows()} rows of ${lever.total}`);

  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await send("Page.navigate", { url: `${BASE}/?n=2#recommended:${P}` });
  await settled();
  const workday = await reply({ board: "workday" });
  const phoneOk = await openBoards();
  await pick("Workday");
  await esc();
  await settled();
  check("phone: the chip is in the strip and filters the list", phoneOk && (await rows()) === workday.total,
    `${await rows()} rows, server ${workday.total}`);
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, String(err?.stack || err));
} finally {
  console.log(JSON.stringify({ checks }));
  await close();
}

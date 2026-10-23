// GOAL33 §1 (tests/test_goal33_live.py): a resume pasted in resumegen shows in Tailored resumes within 2 s and moves
// Queue -> Generating -> Needs you -> Ready live, with no reload, for each person. The fake resumegen streams an
// event for every change (like resumegen's /api/events); job-hunt-agent follows it (rgwatch) and tells the page.
// usage: node goal33_live.mjs <base url> <token> <chrome path> <profile dir> <fake resumegen url> <person,person>
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, RG, PEOPLE] = process.argv.slice(2);
const LIMIT_MS = 2000;
const { send, js, until, errors, checks, check, close } = await start(CHROME, PROFILE_DIR);
const post = async (path, body) => (await fetch(`${RG}${path}`, { method: "POST", body: JSON.stringify(body) })).json();
const row = (company, word) => `[...document.querySelectorAll('#list .row')].some((r) => r.textContent.includes(${JSON.stringify(company)})
  && ${word.startsWith("!") ? "!" : ""}r.textContent.includes(${JSON.stringify(word.replace(/^!/, ""))}))`;
const view = (name) => js(`[...document.querySelectorAll('#list-head button')].find((x) => x.textContent.startsWith(${JSON.stringify(name)}))?.click()`);
const stamp = () => new Date().toISOString().slice(0, 19) + "Z";
async function timed(fn, expr) {
  const t = Date.now();
  await fn();
  const ok = await until(expr, LIMIT_MS + 3000);
  return ok ? Date.now() - t : Infinity;
}
try {
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  for (const pid of PEOPLE.split(",")) {
    await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:${pid}` });
    await until("document.querySelector('#list .row')", 10000);
    await sleep(2500);                                        // the live stream (after the first paint) is open
    await js("document.querySelector('#tabs [data-tab=resumes]').click()");
    await until("document.querySelector('#list-head button')", 5000);
    await view("Queue");
    await sleep(600);
    const company = `ZZ_TEST Live ${pid}`;
    let id;
    const shown = await timed(async () => { id = (await post("/__add", { profile: pid, company, role: "AI Engineer",
      jd_text: "Build LLM systems in Python. ".repeat(10), status: "queued", created_at: stamp() })).id; }, row(company, "in queue"));
    check(`${pid}: a resume made in resumegen shows in Queue within 2 s`, shown <= LIMIT_MS, `${shown} ms`);
    for (const [status, viewName, word] of [["generating", null, "Generating now"], ["needs_you", "Ready", "Needs you"],
      ["ready", null, "!Needs you"]]) {
      const ms = await timed(async () => {
        await post("/__set", { id, status, started_at: stamp() });
        if (viewName) { await sleep(20); await view(viewName); }
      }, row(company, word));
      check(`${pid}: ${status} shows live within 2 s`, ms <= LIMIT_MS, `${ms} ms`);
    }
    check(`${pid}: the page was never reloaded`, await js("return performance.getEntriesByType('navigation')[0].type !== 'reload'"));
    await js("document.querySelector('#tabs [data-tab=saved]').click()");      // §3d: the outside job counts in Saved
    const saved = await until(`/^Saved [1-9]/.test(document.querySelector('#lh-title')?.textContent || '') &&
      document.querySelector('#list-more .section-head') && !/Nothing saved yet/.test(document.body.innerText)`, 8000);
    check(`${pid}: Saved counts its outside jobs and never says Nothing saved yet`, saved,
      await js("return document.querySelector('#list-head')?.innerText + ' | ' + document.querySelector('#list-pane, main')?.innerText.slice(0, 200)"));
    await js("document.activeElement?.blur(); document.dispatchEvent(new KeyboardEvent('keydown', { key: '0', bubbles: true }))");
    check(`${pid}: key 0 opens Answers, the 10th view`, await until("document.querySelector('#tabs [data-tab=answers]')?.getAttribute('aria-selected') === 'true'", 5000));
  }
  check("no page errors", !errors.length, errors.join(" | "));
} finally {
  console.log(JSON.stringify({ checks }));
  close();
}

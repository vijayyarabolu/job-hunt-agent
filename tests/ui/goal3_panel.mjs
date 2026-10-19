// resumegen GOAL3 §4-§6 in job-hunt-agent's UI (tests/test_goal3_panel_ui.py): the tailored match on the ring / row / pane,
// and the resume panel's made time, Outdated badge + Regenerate with current resume, Looks good / Fix with note, Restore.
// usage: node goal3_panel.mjs <base url> <token> <chrome path> <profile dir> <fake resumegen url> <uid,uid,uid>
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, FAKE, UIDS] = process.argv.slice(2);
const { send, js, until, errors, checks, check, clickText, server, close } = await start(CHROME, PROFILE_DIR);
const fake = async (path) => (await fetch(`${FAKE}${path}`)).json();
const lastAction = async () => (await fake("/__state")).actions.at(-1) || [];
const P = "tester";
const uids = UIDS.split(",");
const panel = () => js("return document.querySelector('#detail')?.textContent || ''");
try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:${P}` });
  await until("document.querySelectorAll('#list .row[data-i]').length >= 3 && document.body.classList.contains('rg-on')", 10000);
  const order = (await server(`/api/jobs?profile=${P}&tab=recommended`)).jobs.map((j) => j.uid);
  const row = (uid) => `#job-${order.indexOf(uid)}`;
  const [r0, r1, r2] = uids.map(row);

  // ---- §4 the tailored match ----
  const tailoredTag = `[...document.querySelectorAll('${r0} .tag.soft')].find((t) => /% → \\d+% tailored/.test(t.textContent))`;
  check("the row shows base → tailored", await until(tailoredTag, 8000), await js(`return document.querySelector('${r0}')?.textContent`));
  check("the ring names the tailored resume", await js(`return document.querySelector('${r0} .ring').getAttribute('aria-label').includes('with your tailored resume')`),
    await js(`return document.querySelector('${r0} .ring').getAttribute('aria-label')`));
  check("the other jobs keep their base number", await js(`return ![...document.querySelectorAll('${r1} .tag, ${r2} .tag')].some((t) => t.textContent.includes('tailored'))`));
  await js(`document.querySelector('${r0} .row-title').click()`);
  check("the job pane says it under the ring", await until("document.querySelector('#detail .ring-col')?.textContent.includes('with your tailored resume')", 6000),
    await js("return document.querySelector('#detail .ring-col')?.textContent"));

  // ---- §5 made time + Outdated ----
  check("the chip says Outdated", (await js(`return document.querySelector('${r0} .rgc').textContent`)) === "Resume ready · Outdated",
    await js(`return document.querySelector('${r0} .rgc').textContent`));
  await js(`document.querySelector('${r0} .rgc').click()`);
  await until("document.querySelector('#detail .rg-status')", 6000);
  check("the panel shows when it was made", await js("return [...document.querySelectorAll('#detail .rg-status > *')].some((p) => /^Made [A-Z][a-z]{2} \\d+, .* ago$/.test(p.textContent))"), await panel());
  check("the Outdated badge and its reason", await js("return document.querySelector('#detail .rg-old')?.textContent.includes('Outdated – made from your old resume (made from your old resume)')"), await panel());
  await clickText("#detail .rg-old .btn", "Make it again from my current resume");
  await sleep(600);
  const regen = await lastAction();
  check("Regenerate with current resume", regen[1] === "regenerate" && regen[2]?.current === true, JSON.stringify(regen));

  // ---- §1 needs you: reasons, Fix with note, Looks good ----
  await js(`document.querySelector('${r1} .rgc').click()`);
  await until("document.querySelector('#detail .rg-fails')", 6000);
  check("needs you shows the reasons in plain words", (await js("return document.querySelector('#detail .rg-fails li')?.textContent")) === "Length: page 2 has 3 lines", await panel());
  const before = (await fake("/__state")).actions.length;
  await clickText("#detail .rg-acts .btn", "Fix with note");
  await sleep(300);
  check("Fix with note needs a note", (await fake("/__state")).actions.length === before
    && await js("return [...document.querySelectorAll('.toast p')].some((p) => p.textContent.startsWith('Write what to fix'))"));
  await js("const n = document.querySelector('#detail .rg-note'); n.value = 'only make the summary shorter'; n.dispatchEvent(new Event('input'))");
  await clickText("#detail .rg-acts .btn", "Fix with note");
  await sleep(600);
  const fix = await lastAction();
  check("Fix with note sends the note", fix[1] === "fix" && fix[2]?.note === "only make the summary shorter", JSON.stringify(fix));
  // like resumegen: the fix is queued, then comes back needing a look (the panel follows it by itself)
  await fetch(`${FAKE}/__set`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: fix[0], status: "needs_you" }) });
  await until("[...document.querySelectorAll('#detail .rg-acts .btn')].some((b) => b.textContent === 'Accept as it is' && !b.disabled)", 6000);
  await clickText("#detail .rg-acts .btn", "Accept as it is");
  await sleep(600);
  check("Looks good accepts it", (await lastAction())[1] === "accept", JSON.stringify(await lastAction()));

  // ---- §6 Restore ----
  await js(`document.querySelector('${r2} .rgc').click()`);
  await until("[...document.querySelectorAll('#detail .rg-status .btn')].some((b) => b.textContent === 'Restore')", 6000);
  check("the panel says it was archived", (await panel()).includes("Archived "), await panel());
  await clickText("#detail .rg-status .btn", "Restore");
  await sleep(600);
  const restore = await lastAction();
  check("an archived resume can be restored", restore[1] === "unarchive" && restore[2]?.ids?.length === 1, JSON.stringify(restore));
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, String(err?.stack || err));
} finally {
  console.log(JSON.stringify({ checks }));
  await close();
}

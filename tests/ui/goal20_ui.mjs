// GOAL20 audit fixes (tests/test_goal20_ui.py): one check per problem found in the live walk.
// usage: node goal20_ui.mjs <base url> <token> <chrome path> <profile dir> <data json>
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, DATA] = process.argv.slice(2);
const want = JSON.parse(DATA);
const { send, js, until, key, errors, check, checks, close, state } = await start(CHROME, PROFILE_DIR);
const rg = (path, body) => fetch(want.rg + path, { method: "POST", body: JSON.stringify(body || {}) }).then((r) => r.json());
const S = JSON.stringify;
let loads = 0;
const load = async (hash) => {
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}&n=${++loads}#${hash}` });
  await until("document.querySelectorAll('#list .row[data-i]').length > 0", 8000);
  await sleep(300);
};
const size = (width, height, mobile = false) =>
  send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: mobile ? 3 : 1, mobile });

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await size(1440, 900);
  await load("recommended:tester");
  // the side menu at 1440 px (it was a tab strip where Stats was cut off by the search box): busy days give
  // Tailored resumes / Follow-ups 3-digit counts; every name and count stays whole inside the menu
  const tabs = await js(`for (const b of document.querySelectorAll('#tabs [data-tab]')) {
      let n = b.querySelector('.n'); if (!n) { n = document.createElement('span'); n.className = 'n'; b.append(n); } n.textContent = '999'; }
    await new Promise((r) => requestAnimationFrame(r));
    const nav = document.querySelector('#tabs'); const r = nav.getBoundingClientRect();
    const cut = [...nav.querySelectorAll('[data-tab]')].filter((b) => { const l = b.querySelector('.lbl'), n = b.querySelector('.n'), br = b.getBoundingClientRect();
      return l.scrollWidth > l.clientWidth + 1 || n.getBoundingClientRect().right > br.right + 0.5 || br.right > r.right + 0.5 || br.bottom > innerHeight; }).map((b) => b.dataset.tab);
    return { sw: nav.scrollWidth, cw: nav.clientWidth, cut, last: 0, right: 1 }`);
  check("desktop 1440: every tab fully visible with 3-digit counts", tabs.sw <= tabs.cw && !tabs.cut.length, S(tabs));
  await load("recommended:tester");
  const yrs = await js("return [...document.querySelectorAll('#list .tag.yrs')].map((t) => t.textContent)");
  check("card: Asks 1 yr (not 1 yrs)", yrs.includes("Asks 1 yr") && !yrs.some((t) => /^Asks 1 yrs/.test(t)), S(yrs));
  // Resumes → Ready: an outside job (a JD pasted in resumegen) has no match score and never gets one
  await js("[...document.querySelectorAll('[data-tab=resumes]')].find((b) => b.offsetParent)?.click(); return true");
  await until("[...document.querySelectorAll('#list .row')].some((r) => r.textContent.includes('Globex'))", 8000);
  const side = await js("return [...document.querySelectorAll('#list .row')].find((r) => r.textContent.includes('Globex'))?.querySelector('.row-side')?.textContent");
  check("Resumes: an outside job says 'outside job', not 'scoring'", /outside job/.test(side || "") && !/scoring/.test(side || ""), S(side));
  // Answers on hand (user 23:27): the side panel's chips can be edited in place, saved, then copied
  await load("recommended:tester");
  await js("[...document.querySelectorAll('#list .row')].find((r) => r.querySelector('.row-title')?.textContent === 'AI Engineer')?.click(); return true");
  const ANS = "document.querySelector('#detail .ans')";
  await until(`${ANS}?.querySelector('.chip')`, 8000);
  await js(`[...${ANS}.querySelectorAll('.chip')].find((c) => c.textContent.startsWith('Earliest start'))?.click(); return true`);
  const focused = await until("document.activeElement?.name === 'start' && document.activeElement.closest('#detail .ans')", 3000);
  check("Answers: an empty chip opens an editor on that field, in the panel", focused, await js("return document.activeElement?.outerHTML?.slice(0, 120)"));
  await js("document.activeElement.value = 'Two weeks after an offer'; return true");
  await key("Enter");
  const saved = await until(`[...${ANS}.querySelectorAll('.chip:not(.ghost)')].some((c) => c.textContent === 'Earliest start')`, 4000);
  const server = await js("return (await (await fetch('/api/settings?profile=tester')).json()).answers?.start");
  check("Answers: Enter saves it and the chip is filled", saved && server === "Two weeks after an offer", S({ saved, server }));
  await js(`[...${ANS}.querySelectorAll('button')].find((b) => b.textContent === 'Edit')?.click(); return true`);
  const pre = await until(`${ANS}.querySelector('input[name=start]')?.value === 'Two weeks after an offer'`, 3000);
  check("Answers: Edit shows every answer filled in", pre && await js(`return ${ANS}.querySelectorAll('input').length`) === 7, S(pre));
  await js(`${ANS}.querySelector('input[name=start]').value = 'not this'; [...${ANS}.querySelectorAll('button')].find((b) => b.textContent === 'Cancel').focus(); return true`);
  for (const type of ["keyDown", "keyUp"]) await send("Input.dispatchKeyEvent", { type, key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: type === "keyDown" ? "\r" : undefined });
  await sleep(600);
  const kept = await js("return (await (await fetch('/api/settings?profile=tester')).json()).answers?.start");
  check("Answers: Enter on Cancel cancels (saves nothing)", kept === "Two weeks after an offer" && !(await js(`return !!${ANS}.querySelector('input')`)), S(kept));
  await js(`[...${ANS}.querySelectorAll('button')].find((b) => b.textContent === 'Edit')?.click(); return true`);
  await until(`${ANS}.querySelector('input[name=start]')`, 3000);
  await js(`${ANS}.querySelector('input[name=start]').focus(); return true`);
  await key("Escape");
  const esc = await until(`!${ANS}.querySelector('input') && document.querySelector('#detail .d-title')`, 3000);
  check("Answers: Esc cancels the edit and keeps the job open", esc, "");
  await js(`[...${ANS}.querySelectorAll('.chip')].find((c) => c.textContent === 'Earliest start')?.click(); return true`);
  const toastOk = await until("[...document.querySelectorAll('.toast p')].some((p) => p.textContent.includes('Two weeks after an offer'))", 3000);
  check("Answers: a chip copies and the toast shows what was copied", toastOk, await js("return [...document.querySelectorAll('.toast p')].map((p) => p.textContent).join(' | ')"));
  // Re-import / Fix with note (user 22:21, 22:23): the button stays busy until the NEW resume is on screen
  await load("recommended:tester");
  await until("[...document.querySelectorAll('#list .row')].some((r) => r.querySelector('.rgc.rg-ready'))", 10000);
  await js("[...document.querySelectorAll('#list .row')].find((r) => r.querySelector('.rgc.rg-ready')).querySelector('.rgc').click(); return 1");
  await until("document.querySelector('#detail .rg-acts')", 6000);
  await sleep(800);
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/resumes/file*" }] });
  await rg("/__set", { id: want.rg_id, files_version: "2026-10-12T21:30:00Z" });
  state.holdNext = { url: "/api/resumes/file", body: "", ms: 3000 };       // the new PDF arrives 3 s late
  const BTN = (t) => `[...document.querySelectorAll('#detail .rg-acts button')].find((b) => b.textContent.startsWith('${t}'))`;
  await js(`${BTN("Get my Doc edits")}.click(); return 1`);
  await until("document.querySelector('#detail iframe.rg-pdf')?.src.includes('21%3A30')", 4000);
  await sleep(1300);
  const mid = await js(`const b = ${BTN("Getting your Doc edits")}; return { b: !!b, dis: b?.disabled, busy: b?.getAttribute('aria-busy'), text: b?.textContent,
    others: [...document.querySelectorAll('#detail .rg-acts button')].filter((x) => x !== b).map((x) => x.disabled),
    line: document.querySelector('#detail .rg-busy')?.textContent }`);
  check("Re-import: busy (disabled, 'Re-importing', seconds) while the new PDF loads", mid.b && mid.dis && mid.busy === "true" && /\d+ s$/.test(mid.text), S(mid));
  check("Re-import: the other actions wait", mid.others.length > 0 && mid.others.every(Boolean), S(mid));
  check("Re-import: a status line says what is running", /^Getting your Doc edits/.test(mid.line || ""), S(mid));
  const after = await until(`${BTN("Get my Doc edits")} && !${BTN("Get my Doc edits")}.disabled && !document.querySelector('#detail .rg-busy')`, 6000);
  check("Re-import: back to normal once the new PDF has loaded", after, await js("return [...document.querySelectorAll('#detail .rg-acts button')].map((b) => b.textContent + (b.disabled ? '(off)' : ''))"));
  // the busy button says the Re-import's step while it runs (resumegen tells it in the job view)
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/resumes/action*" }] });
  await rg("/__set", { id: want.rg_id, step: { step: "building the PDF", since: 1, at: 2 } });
  state.holdNext = { url: "/api/resumes/action", body: "reimport", ms: 3000 };
  await js(`${BTN("Get my Doc edits")}.click(); return 1`);
  const told = await until(`${BTN("Getting your Doc edits")}?.textContent.includes('building the PDF')`, 2800);
  check("Re-import: the busy button says the step it is on", told, await js(`return ${BTN("Getting your Doc edits")}?.textContent`));
  await rg("/__set", { id: want.rg_id, step: null });
  await until(`${BTN("Get my Doc edits")} && !${BTN("Get my Doc edits")}.disabled`, 8000);
  await send("Fetch.disable");
  // Fix with note runs in the worker (Claude): busy until the job is back with its new file
  await js("document.querySelector('#detail .rg-note').value = 'only make the summary shorter'; return 1");
  await rg("/__set", { id: want.rg_id, status: "queued" });
  await js(`${BTN("Fix with note")}.click(); return 1`);
  const line = await until("/^Fixing with your note/.test(document.querySelector('#detail .rg-busy')?.textContent || '')", 4000);
  check("Fix with note: busy line while the worker runs", line, await js("return document.querySelector('#detail')?.innerText.slice(0, 300)"));
  await sleep(1500);
  await rg("/__set", { id: want.rg_id, status: "ready", files_version: "2026-10-12T22:00:00Z" });
  const fixed = await until("!document.querySelector('#detail .rg-busy') && document.querySelector('#detail iframe.rg-pdf')?.src.includes('22%3A00')", 15000);
  check("Fix with note: ends when the new resume is on screen", fixed, await js("return [document.querySelector('#detail .rg-busy')?.textContent, document.querySelector('#detail iframe.rg-pdf')?.src]"));
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
}
close();
console.log(JSON.stringify({ checks }));

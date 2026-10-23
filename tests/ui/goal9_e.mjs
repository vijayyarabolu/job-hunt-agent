// GOAL9 area E in headless Chrome against a throwaway DB and a fake resumegen (tests/test_goal9_e.py): the resume
// side panel (opens at once, Esc works with the PDF focused, focus returns, actions go busy / optimistic, chips agree
// everywhere, no stale panel), Settings (a page since GOAL21: inline validation, the feed re-ranks without a reload), "Did you apply?" on
// the phone (inside the viewport, focus trapped), the phone sheet (open / swipe / Esc / focus, the sticky bar inside
// 390 px, the panel returns to the sheet), the More menu close rules, the ? sheet, the pick bar, standalone-mode
// flows, the offline Resume app, and GOAL9 §6 hover labels on every control of the area (tests/ui/tips.mjs).
// usage: node goal9_e.mjs <base url> <token> <chrome path> <profile dir> <fake resumegen url> [screenshot dir]
import { writeFileSync } from "node:fs";
import { sleep, start } from "./cdp.mjs";
import { hoverTip, missingTips } from "./tips.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, FAKE, SHOTS] = process.argv.slice(2);
const { send, js, until, key, errors, checks, check, click, clickText, server, state, close } = await start(CHROME, PROFILE_DIR);
const fake = async (path, body) => (await fetch(`${FAKE}${path}`, body ? { method: "POST", body: JSON.stringify(body) } : {})).json();
const P = "tester";
const FAST_MS = 150;
const PHONE = { width: 390, height: 844 };
const chip = (i) => js(`return document.querySelector('#job-${i} .rgc')?.textContent || ''`);
const detailChip = () => js("return document.querySelector('#detail .acts .rgc')?.textContent || ''");
const panelText = () => js("return document.querySelector('#detail .d-in.panel')?.textContent || ''");
const dialogText = () => js("return document.querySelector('dialog.back-dlg[open]')?.textContent || ''");
const active = () => js("const a = document.activeElement; return a ? a.tagName + (a.id ? '#' + a.id : '') + (a.className ? '.' + a.className.split(' ')[0] : '') + (a.dataset?.uid ? '[uid]' : '') : 'none'");
const inside = (sel) => js(`return [...document.querySelectorAll(${JSON.stringify(sel)})].filter((e) => e.getClientRects().length).every((e) => { const b = e.getBoundingClientRect(); return b.left >= -0.5 && b.right <= innerWidth + 0.5 && b.top >= -0.5 && b.bottom <= innerHeight + 0.5; })`);
const boxes = (sel) => js(`return [...document.querySelectorAll(${JSON.stringify(sel)})].filter((e) => e.getClientRects().length).map((e) => { const b = e.getBoundingClientRect(); return [e.textContent.trim().slice(0, 16) || e.getAttribute('aria-label') || e.tagName, Math.round(b.left), Math.round(b.right), Math.round(b.bottom)]; })`);
const tips = async (name, scope, ignore = "") => { const miss = await missingTips(js, scope, ignore); check(`labels: ${name}`, miss.length === 0, miss.join(" || ")); };
const shot = async (name) => { if (!SHOTS) return; const r = await send("Page.captureScreenshot", { format: "png" }); writeFileSync(`${SHOTS}/${name}.png`, Buffer.from(r.data, "base64")); };
// click -> the first frame in which doneExpr holds, measured in the page (rAF), -1 when it never does
const timed = (clickExpr, doneExpr, ms = 4000) => js(`const t0 = performance.now(); ${clickExpr};
  return await new Promise((res) => { const tick = () => { if (${doneExpr}) res(Math.round((performance.now() - t0) * 10) / 10);
    else if (performance.now() - t0 > ${ms}) res(-1); else requestAnimationFrame(tick); }; tick(); });`);
const fast = (name, ms) => check(name, ms >= 0 && ms < FAST_MS, `${ms} ms`);
const desktop = () => send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
const phone = async () => { await send("Emulation.setDeviceMetricsOverride", { ...PHONE, deviceScaleFactor: 2, mobile: true }); await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 }); };
const centre = (sel) => js(`const el = [...document.querySelectorAll(${JSON.stringify(sel)})].find((x) => x.getClientRects().length); if (!el) return null; const b = el.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]`);
const touch = (type, points) => send("Input.dispatchTouchEvent", { type, touchPoints: points });
const tap = async (sel) => { const c = await centre(sel); if (!c) return false; await touch("touchStart", [{ x: c[0], y: c[1] }]); await touch("touchEnd", []); await sleep(150); return true; };
const swipeDown = async (x, y0, dy) => { await touch("touchStart", [{ x, y: y0 }]); for (let i = 1; i <= 6; i++) await touch("touchMove", [{ x, y: y0 + (dy * i) / 6 }]); await touch("touchEnd", []); await sleep(450); };
const back = async () => { await send("Page.bringToFront"); await sleep(150); await js("document.dispatchEvent(new Event('visibilitychange'))"); };
let loads = 0;
const load = async (hash) => { await send("Page.navigate", { url: `${BASE}/?n=${++loads}#${hash}` }); await sleep(300);
  await until("document.querySelector('#list-head h1')?.textContent && !document.querySelector('#list[aria-busy]')", 8000);
  await until("document.body.classList.contains('rg-on') || document.querySelector('#detail .rg-off')", 8000); };
const answerAll = async () => { for (let i = 0; i < 4 && await js("return !!document.querySelector('dialog.back-dlg[open]')"); i++) { await clickText("dialog.back-dlg .btn", "No"); await sleep(300); } };

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Fetch.enable", { patterns: [{ urlPattern: "*/api/*", requestStage: "Request" }] });
  await desktop();
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:${P}` });
  check("feed loads", await until("document.querySelectorAll('#list .row[data-i]').length >= 5", 8000));
  check("the resume app connects", await until("document.body.classList.contains('rg-on')", 8000));
  const uids = (await server(`/api/jobs?profile=${P}&tab=recommended`)).jobs.map((j) => j.uid);
  const JD = "Requirements: Python, PyTorch, Kubernetes. ".repeat(4);
  const files = { pdf: "/x.pdf", png: "/x.png" };
  const ready = { base: { id: "ml", label: "ML Engineer" }, ats: { base_pct: 60, tailored_pct: 74, label: "Greenhouse estimate", missing: ["gRPC"] }, files,
    diff: { changed_bullets: 1, total_bullets: 5, summary: { changed: true, ops: [["equal", "ML Engineer with", "ML Engineer with"], ["replace", "4 years", "4 years building"]] } } };
  const ids = {};
  ids.ready = (await fake("/__add", { profile: P, company: "Acme", role: "MLE", jd_text: JD, source_job_id: uids[0], status: "ready", attempts: 1, elapsed_s: 240, result: ready })).id;
  ids.queued = (await fake("/__add", { profile: P, company: "Acme", role: "Senior MLE", jd_text: JD, source_job_id: uids[1], status: "queued" })).id;
  ids.generating = (await fake("/__add", { profile: P, company: "Acme", role: "AI Engineer", jd_text: JD, source_job_id: uids[2], status: "generating", attempts: 1 })).id;
  ids.needs = (await fake("/__add", { profile: P, company: "Acme", role: "ML Platform", jd_text: JD, source_job_id: uids[3], status: "failed", attempts: 2,
    error: "ATS check failed twice", result: { files, checks: { failures: [{ message: "Tailored ATS 52% below base 60%" }] } } })).id;
  ids.approved = (await fake("/__add", { profile: P, company: "Acme", role: "Applied AI", jd_text: JD, source_job_id: uids[4], status: "ready", attempts: 1,
    drive_file_id: "F9", drive_link: "https://drive.example/F9", drive_name: "Tess.pdf", gdoc_link: "https://docs.example/d/9",
    result: { ...ready, approved: { link: "https://drive.example/F9", name: "Tess.pdf" } } })).id;
  await js("document.dispatchEvent(new Event('visibilitychange'))");       // a poll now
  check("chips paint", await until("document.querySelector('#job-0 .rgc')?.textContent === 'Resume ready' && document.querySelector('#job-4 .rgc')?.textContent === 'Resume approved'", 9000),
    [await chip(0), await chip(4)].join());

  // ---- the resume panel (1440) ----
  fast("row chip -> panel opens at once", await timed("document.querySelector('#job-0 .rgc').click()", "document.querySelector('#detail .d-in.panel')"));
  check("the panel fills", await until("document.querySelector('#detail iframe.rg-pdf') && document.querySelector('#detail .rg-diff ins')"), (await panelText()).slice(0, 200));
  await sleep(1500);
  check("the panel keeps the focus off the PDF viewer", (await active()) !== "IFRAME.rg-pdf", await active());
  await shot("panel-1440");
  await tips("panel (ready)", "#detail");
  const approveTip = await hoverTip(send, js, sleep, "#detail .rg-acts .btn.primary");
  check("hover label: Approve", approveTip.text === "Approve this resume: upload it to Drive" && !approveTip.overlaps, JSON.stringify(approveTip));
  const closeTip = await hoverTip(send, js, sleep, "#detail .icon-btn.close");
  check("hover label: the panel close", closeTip.text === "Close the resume panel (Esc)" && !closeTip.overlaps, JSON.stringify(closeTip));
  await key("Escape");
  check("Esc closes the panel and restores the detail", await until("!document.body.classList.contains('panel-open') && document.querySelector('#detail .d-title')"));
  check("focus is back on the list after Esc", (await active()) === "OL#list.list", await active());
  await until("document.querySelector('#detail .acts .rgc')?.textContent === 'Resume ready'");
  fast("detail chip -> panel", await timed("document.querySelector('#detail .acts .rgc').click()", "document.querySelector('#detail .d-in.panel')"));
  await until("document.querySelector('#detail .rg-note')");
  await click("#detail .icon-btn.close");
  await sleep(100);
  check("the close button gives the focus back to the detail chip", (await active()) === "BUTTON.rgc[uid]", await active());
  // keyboard focus shows the label
  await js("[...document.querySelectorAll('#detail .acts .btn')].find((b) => b.textContent === 'Mark applied').focus()");
  await key("Tab");
  await sleep(450);
  check("keyboard focus shows the label", (await js("return document.getElementById('tip')?.hidden === false ? document.getElementById('tip').textContent : ''")) === "Generate a tailored resume (g)",
    await js("return [document.activeElement.textContent, document.getElementById('tip')?.textContent]"));
  await key("Escape");

  // Regenerate + note: optimistic everywhere, the note arrives, rollback when refused
  await js("document.querySelector('#job-0 .rgc').click()");
  await until("document.querySelector('#detail .rg-note')");
  await js("document.querySelector('#detail .rg-note').value = 'more MLOps'");
  fast("Regenerate: the row chip says queued at once", await timed("[...document.querySelectorAll('#detail .btn')].find((b) => b.textContent === 'Start over').click()",
    "document.querySelector('#job-0 .rgc')?.textContent === 'Resume queued'"));
  check("and the panel says queued at once", (await panelText()).includes("Resume queued"), (await panelText()).slice(0, 120));
  await sleep(700);
  let st = await fake("/__state");
  check("the resume app gets the note", st.actions.some(([id, a, b]) => id === ids.ready && a === "regenerate" && b.note === "more MLOps"), JSON.stringify(st.actions));
  check("the chip stays queued once the app answers", (await chip(0)) === "Resume queued" && (await panelText()).includes("Resume queued"), await chip(0));
  await key("Escape");
  await until("document.querySelector('#detail .acts .rgc')");
  check("the detail chip agrees", (await detailChip()) === "Resume queued" && (await js("return document.querySelector('#detail .rg-gen-label')?.textContent")) === "Generate again", await detailChip());
  await js("document.querySelector('#job-3 .rgc').click()");
  await until("[...document.querySelectorAll('#detail .btn')].some((b) => b.textContent === 'Start over')");
  await shot("panel-needs-you");
  await tips("panel (needs you)", "#detail");
  state.failNext = "/api/resumes/action";
  await js("[...document.querySelectorAll('#detail .btn')].find((b) => b.textContent === 'Start over').click()");
  check("a refused Regenerate rolls the chip back with a toast", await until("document.querySelector('#job-3 .rgc')?.textContent === 'Resume needs you' && [...document.querySelectorAll('.toast.err p')].some((p) => p.textContent.includes('server said no'))"),
    await chip(3));
  check("and the button is usable again", await js("const b = [...document.querySelectorAll('#detail .btn')].find((x) => x.textContent === 'Start over'); return !!b && !b.disabled"));
  await key("Escape");

  // Approve: busy at once, never twice, then everything says approved
  await fake("/__set", { id: ids.ready, status: "ready" });
  await js("document.dispatchEvent(new Event('visibilitychange'))");
  await until("document.querySelector('#job-0 .rgc')?.textContent === 'Resume ready'", 9000);
  await js("document.querySelector('#job-0 .rgc').click()");
  await until("[...document.querySelectorAll('#detail .btn')].some((b) => b.textContent === 'Approve')");
  fast("Approve goes busy at once", await timed("[...document.querySelectorAll('#detail .btn')].find((b) => b.textContent === 'Approve').click()",
    "[...document.querySelectorAll('#detail .btn')].some((b) => b.disabled && b.getAttribute('aria-busy') === 'true')"));
  await js("[...document.querySelectorAll('#detail .btn')].find((b) => b.textContent === 'Approve')?.click()");
  check("the panel shows Approved after the upload", await until("[...document.querySelectorAll('#detail a.btn')].some((a) => a.textContent.startsWith('Approved'))", 6000), (await panelText()).slice(0, 160));
  st = await fake("/__state");
  check("a second click sends nothing", st.actions.filter(([id, a]) => id === ids.ready && a === "approve").length === 1, JSON.stringify(st.actions));
  check("the row chip says approved", await until("document.querySelector('#job-0 .rgc')?.textContent === 'Resume approved'", 6000), await chip(0));
  await tips("panel (approved)", "#detail");
  await sleep(1200);                                   // the re-rendered PDF frame loads (it grabs the focus; the panel takes it back)
  check("the re-rendered PDF does not keep the focus", await until("document.activeElement.tagName !== 'IFRAME'"), await active());
  await key("Escape");
  await until("document.querySelector('#detail .acts .rgc')");
  check("the detail chip says approved too", (await detailChip()) === "Resume approved",
    await js("return JSON.stringify({ chip: document.querySelector('#detail .acts .rgc')?.textContent, panel: document.body.classList.contains('panel-open'), title: document.querySelector('#detail .d-title')?.textContent, active: document.activeElement.tagName + '.' + document.activeElement.className })"));
  await js("document.querySelector('#job-4 .rgc').click()");
  await until("[...document.querySelectorAll('#detail .btn')].some((b) => b.textContent === 'Get my Doc edits')");
  fast("Re-import goes busy at once", await timed("[...document.querySelectorAll('#detail .btn')].find((b) => b.textContent === 'Get my Doc edits').click()",
    "[...document.querySelectorAll('#detail .btn')].some((b) => b.disabled && b.getAttribute('aria-busy') === 'true')"));
  await until("[...document.querySelectorAll('#detail .btn')].some((b) => b.textContent === 'Get my Doc edits' && !b.disabled)");
  await key("Escape");

  // the panel refreshes when the status moves; a closed panel never comes back by itself
  await js("document.querySelector('#job-1 .rgc').click()");
  await until("document.querySelector('#detail .d-in.panel')?.textContent.includes('Resume queued')");
  await tips("panel (queued)", "#detail");
  await fake("/__set", { id: ids.queued, status: "ready", attempts: 1, result: ready });
  check("the open panel refreshes when the status moves", await until("document.querySelector('#detail .d-in.panel')?.textContent.includes('Resume ready')", 9000), (await panelText()).slice(0, 100));
  await key("Escape");
  await fake("/__set", { id: ids.queued, status: "generating" });
  await until("document.querySelector('#job-1 .rgc')?.textContent === 'Generating resume'", 9000);
  check("a closed panel stays closed when its status moves", await until("!document.querySelector('#detail .d-in.panel') && document.querySelector('#detail .d-title')", 3000),   // the job's detail reloads async after Esc
    await js("const d = document.querySelector('#detail'); return `active=${document.activeElement?.tagName}.${document.activeElement?.className} panel=${!!d.querySelector('.d-in.panel')} title=${!!d.querySelector('.d-title')} body=${document.body.className} cur=${document.querySelector('#list .row[aria-selected=true]')?.id} text=${d.textContent.slice(0, 120)}`"));

  // ---- Settings (a page): inline validation, saves on its own, the feed re-ranks on the way back, no reload ----
  const SET_OPEN = "document.body.dataset.view === 'settings' && document.querySelector('.set-nav')";
  const capInput = "document.querySelector('.set-body input[type=number]')";
  const setCap = async (v) => { await js(`const i = ${capInput}; i.value = '${v}'; i.dispatchEvent(new Event('change')); return true`); await sleep(200); };
  const toFilters = async () => { await js("document.querySelector('.set-nav [data-sec=filters]').click(); return true"); await until(capInput, 4000); };
  await js("window.__samePage = true");
  const rowsBefore = await js("return document.querySelectorAll('#list .row[data-i]').length");
  await click("#who");
  await tips("profile menu Settings…", "#pop .opt[title^='Your answers']");
  fast("Settings… opens the page", await timed("[...document.querySelectorAll('#pop .opt')].find((o) => o.textContent.startsWith('Settings')).click()", SET_OPEN));
  await toFilters();
  await shot("settings-1440");
  await tips("Settings", ".set-page");
  await setCap(40);
  check("an out-of-range cap shows an inline message and stays open", await js(`const n = document.querySelector('.set-body .saved-state.err'); return ${SET_OPEN} && !!n && n.textContent.includes('0 to 30')`),
    await js("return document.querySelector('.set-body .saved-state')?.textContent"));
  check("without a request", (await server(`/api/settings?profile=${P}`)).max_required_years === 4);
  await setCap(1);
  check("the cap saves on its own", await until("document.querySelector('.set-body .saved-state')?.textContent === 'Saved ✓'", 6000));
  await js("window.__busySeen = false; new MutationObserver(() => { if (document.querySelector('#list[aria-busy]')) window.__busySeen = true; }).observe(document.querySelector('#list'), { attributes: true })");
  fast("Back closes Settings at once", await timed("document.querySelector('.set-back').click()", "document.body.dataset.view === 'jobs'"));
  check("the list shows it is re-ranking", await until("window.__busySeen"));
  check("the feed re-ranks with the new cap, no reload", await until(`!document.querySelector('#list[aria-busy]') && document.querySelectorAll('#list .row[data-i]').length < ${rowsBefore} && window.__samePage`, 8000),
    await js("return [document.querySelectorAll('#list .row[data-i]').length, document.querySelector('#foot').textContent]"));
  check("the cap is saved", (await server(`/api/settings?profile=${P}`)).max_required_years === 1);
  await click("#who");
  await js("[...document.querySelectorAll('#pop .opt')].find((o) => o.textContent.startsWith('Settings')).click()");
  await until(SET_OPEN);
  await toFilters();
  await setCap(4);
  await until("document.querySelector('.set-body .saved-state')?.textContent === 'Saved ✓'", 6000);
  await key("Escape");
  await until(`!document.querySelector('#list[aria-busy]') && document.querySelectorAll('#list .row[data-i]').length === ${rowsBefore}`, 8000);
  await click("#who");
  await js("[...document.querySelectorAll('#pop .opt')].find((o) => o.textContent.startsWith('Settings')).click()");
  await until(SET_OPEN);
  await key("Escape");
  check("Esc closes Settings", await until("document.body.dataset.view !== 'settings'", 3000));

  // ---- phone: the sheet, the panel from the sheet, swipe / Esc / focus, the sticky bar inside 390 px ----
  await phone();
  await load(`recommended:${P}`);
  await answerAll();
  await until("document.querySelector('#job-0 .rgc')?.textContent === 'Resume approved'", 9000);
  // long press on a bottom tab: the label shows inside the viewport, the tab does not switch
  const savedTab = await centre("#btabs [data-tab=stats]");     // the bar has the sections (Jobs, Resumes, Applications, Stats, More)
  await touch("touchStart", [{ x: savedTab[0], y: savedTab[1] }]);
  await sleep(650);
  check("long press shows the label inside the viewport", await js("const t = document.getElementById('tip'); const b = t.getBoundingClientRect(); return !t.hidden && t.textContent === 'Stats' && b.left >= 0 && b.right <= innerWidth && b.top >= 0"),
    await js("const t = document.getElementById('tip'); return [t.hidden, t.textContent, JSON.stringify(t.getBoundingClientRect())]"));
  await touch("touchEnd", []);
  await sleep(300);
  check("letting go hides it and the tab did not switch", await js("return document.getElementById('tip').hidden && document.querySelector('#btabs [data-tab=recommended]').getAttribute('aria-current') === 'page' && !document.querySelector('#btabs [data-tab=stats]').getAttribute('aria-current')"),
    await js("return [document.getElementById('tip').hidden, document.body.dataset.view, document.querySelector('#list-head h1').textContent]"));
  await sleep(700);                                        // the browser's mouseover after a touch must not re-arm a hover tip
  check("the label stays hidden after the long press ends", await js("return document.getElementById('tip').hidden"), await js("return document.getElementById('tip').textContent"));
  const tapTab = async (tab) => {
    const c = await centre(`#btabs [data-tab=${tab}]`);
    await touch("touchStart", [{ x: c[0], y: c[1] }]);
    await sleep(60);
    await touch("touchEnd", []);
  };
  await tapTab("stats");
  await sleep(800);
  check("a short tap shows no label", await until("document.body.dataset.view === 'stats' && document.querySelector('#btabs [data-tab=stats]').getAttribute('aria-current')", 3000)
    && await js("return document.getElementById('tip').hidden"), await js("return document.getElementById('tip').textContent"));
  await tapTab("recommended");
  await until("document.querySelector('#btabs [data-tab=recommended]').getAttribute('aria-current') && document.querySelector('#job-0') && !document.querySelector('#list[aria-busy]')", 8000);
  await sleep(300);
  // touch first, before any mouse hover moves Chrome's emulated pointer (a phone has no mouse)
  await tips("bottom tabs", "#btabs");
  const tabTip = await hoverTip(send, js, sleep, "#btabs [data-tab=stats]");
  check("hover label: a bottom tab", tabTip.text === "Stats" && !tabTip.overlaps, JSON.stringify(tabTip));
  await js("document.getElementById('tip').hidden = true");
  fast("a row opens the sheet at once", await timed("document.querySelector('#job-0').click()", "document.body.classList.contains('sheet-open')"));
  await until("document.querySelector('#detail .acts .rgc')?.textContent === 'Resume approved'");
  await sleep(350);
  await shot("phone-sheet");
  check("the sheet takes the focus", (await active()).startsWith("SECTION#detail"), await active());
  check("the sticky bar stays inside the screen", await inside("#detail .acts > *, #detail .acts .acts-icons > *"), JSON.stringify(await boxes("#detail .acts > *, #detail .acts .acts-icons > *")));
  check("the ⋯ button is reachable by thumb", await js("const b = document.querySelector('#detail .acts [aria-haspopup=menu]').getBoundingClientRect(); return b.right <= innerWidth && b.bottom <= innerHeight && b.height >= 44"));
  await tips("phone sheet (grab + sticky bar)", "#detail .grab, #detail .acts");      // the rest of the pane is area C's
  const grabTip = await hoverTip(send, js, sleep, "#detail .grab");
  check("hover label: the sheet grab", grabTip.text === "Close (Esc), or swipe down" && !grabTip.overlaps, JSON.stringify(grabTip));
  await js("document.getElementById('tip').hidden = true");
  await tap("#detail .acts [aria-haspopup=menu]");
  check("the sheet's ⋯ menu opens inside the viewport", await js("return !document.querySelector('#pop').hidden") && await inside("#pop"));
  await tap("#detail .d-title");
  check("and closes on an outside tap", await js("return document.querySelector('#pop').hidden"));
  fast("the sheet's chip opens the panel", await timed("document.querySelector('#detail .acts .rgc').click()", "document.querySelector('#detail .d-in.panel')"));
  await until("document.querySelector('#detail img.rg-png')");
  check("the phone panel shows the page image, not a PDF frame", await js("return !!document.querySelector('#detail img.rg-png') && !document.querySelector('#detail iframe')"));
  await shot("phone-panel");
  await tips("phone panel", "#detail");
  await key("Escape");
  await sleep(100);
  check("closing the panel brings the job sheet back", await js("return document.body.classList.contains('sheet-open') && !document.querySelector('#detail .d-in.panel') && !!document.querySelector('#detail .d-title')"),
    await js("return [document.body.classList.contains('sheet-open'), document.querySelector('#detail .d-title')?.textContent]"));
  await swipeDown(195, 30, 220);
  check("swipe down closes the sheet and focuses the list", !(await js("return document.body.classList.contains('sheet-open')")) && (await active()) === "OL#list.list", await active());
  await js("document.querySelector('#job-1').click()");
  await until("document.body.classList.contains('sheet-open')");
  await sleep(350);
  fast("Esc closes the sheet at once", await timed("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))", "!document.body.classList.contains('sheet-open')"));
  check("with the focus back on the list", (await active()) === "OL#list.list", await active());
  await sleep(400);                                                   // the sheet's exit transition

  // the More menu: close rules, labels, Settings… from it
  await tap("#btabs [data-tab=more]");
  check("More opens", await js("return !document.querySelector('#pop').hidden && [...document.querySelectorAll('#pop .opt')].some((o) => o.textContent.startsWith('Settings'))"));
  await tips("More menu", "#pop");
  await shot("phone-more-menu");
  await tap("#list-head h1");
  check("More closes on an outside tap", await js("return document.querySelector('#pop').hidden"));
  await tap("#btabs [data-tab=more]");
  await sleep(100);
  await key("Escape");
  check("More closes on Esc with the focus back on it", await js("return document.querySelector('#pop').hidden && document.activeElement === document.querySelector('#btabs [data-tab=more]')"));
  await tap("#btabs [data-tab=more]");
  await sleep(100);
  await tap("#btabs [data-tab=more]");
  check("a second tap on More closes it", await js("return document.querySelector('#pop').hidden"));
  await tap("#btabs [data-tab=more]");
  await sleep(100);
  await js("[...document.querySelectorAll('#pop .opt')].find((o) => o.textContent.startsWith('Settings')).click()");
  check("More -> Settings… closes the menu and opens the page inside the viewport", await until("document.body.dataset.view === 'settings' && document.querySelector('.set-nav') && document.querySelector('#pop').hidden")
    && await js("return [...document.querySelectorAll('.set-top *, .set-body *')].filter((e) => e.getClientRects().length).every((e) => { const b = e.getBoundingClientRect(); return b.left >= -0.5 && b.right <= innerWidth + 0.5; }) && document.scrollingElement.scrollWidth <= innerWidth"),
    await js("return document.querySelector('.set-page')?.outerHTML.slice(0, 120) || document.body.dataset.view"));   // the section chips scroll sideways inside their strip
  await shot("phone-settings");
  await tips("phone Settings", ".set-page");
  await key("Escape");
  await until("document.body.dataset.view !== 'settings'");
  await key("?");
  check("the ? sheet fits the phone", await until("document.querySelector('#help[open]')") && await inside("#help, #help .btn"));
  await tips("? sheet", "#help");
  await key("Escape");
  await until("document.querySelector('.sel-mode')");
  await tap(".sel-mode");
  await until("document.body.classList.contains('pick-mode')");
  await tap("#job-2 .pick");
  await js("document.querySelector('#job-3').scrollIntoView({ block: 'center' }); return true");   // the pick bar now covers the screen's foot
  await sleep(200);
  await tap("#job-3 .pick");
  check("the pick bar sits inside the viewport", await until("document.querySelector('.pickbar:not([hidden]) .pb-n')?.textContent === '2 selected'") && await inside(".pickbar, .pickbar .btn"),
    JSON.stringify([await js("return document.querySelector('.pickbar .pb-n')?.textContent"), await boxes(".pickbar, .pickbar .btn"), await js("return innerHeight")]));
  await tips("pick bar + Select", ".pickbar, #list-head .sel-mode");
  await shot("phone-pickbar");
  await tap(".pickbar .btn.ghost");
  await tap(".sel-mode");

  // ---- "Did you apply?" on the phone ----
  await js("document.querySelector('#job-2').click()");
  check("the sheet opens for the Apply click", await until("document.body.classList.contains('sheet-open') && [...document.querySelectorAll('#detail .acts .btn')].some((b) => b.textContent.startsWith('Apply'))"),
    await js("return [document.body.className, [...document.querySelectorAll('#detail .acts .btn')].map((b) => b.textContent)]"));
  await sleep(350);
  await js("[...document.querySelectorAll('#detail .acts .btn')].find((b) => b.textContent.startsWith('Apply')).click()");
  await sleep(300);
  check("Apply ↗ is remembered", (await server(`/api/pending?profile=${P}&peek=1&boot=1`)).ask?.length === 1, JSON.stringify(await server(`/api/pending?profile=${P}&peek=1&boot=1`)));
  await back();
  // the job is on the open list, so it is asked at the top of its sheet; off the list, by the dialog
  check("coming back asks Applied? at the top of the sheet", await until("document.querySelector('#detail .d-head .ask-in')", 6000)
    && await inside("#detail .d-head .ask-in .mini"), JSON.stringify(await boxes("#detail .d-head .ask-in .mini")));
  await key("Escape");
  await sleep(300);
  const toTab = async (tab) => { await js(`document.querySelector('#btabs [data-tab=${tab}]').click()`); await sleep(600); };
  await toTab("tracker");
  await back();
  check("coming back asks Did you apply? on the phone", await until("document.querySelector('dialog.back-dlg[open]')", 6000), await dialogText());
  check("the dialog and its buttons fit the 390 px screen", await inside("dialog.back-dlg, dialog.back-dlg .btn, dialog.back-dlg h2"), JSON.stringify(await boxes("dialog.back-dlg .btn")));
  check("its buttons are 44 px", await js("return [...document.querySelectorAll('dialog.back-dlg .btn')].every((b) => b.getBoundingClientRect().height >= 44)"));
  await shot("phone-did-you-apply");
  await tips("Did you apply?", "dialog.back-dlg[open]");
  check("the focus starts on Yes", (await active()) === "BUTTON.btn" && (await js("return document.activeElement.textContent")) === "Yes, I applied", await active());
  for (let i = 0; i < 4; i++) await key("Tab");
  check("Tab stays inside the dialog", await js("return !!document.activeElement.closest('dialog.back-dlg')"), await active());
  fast("Later reacts at once", await timed("[...document.querySelectorAll('dialog.back-dlg .btn')].find((b) => b.textContent === 'Later').click()", "document.querySelector('dialog.back-dlg .btn:disabled') || !document.querySelector('dialog.back-dlg[open]')"));
  await until("!document.querySelector('dialog.back-dlg[open]')");
  check("Later puts a badge on Applications", await until("document.querySelector('#btabs [data-tab=tracker] .n')?.textContent === '1'"));   // on Applications (Follow-ups is there)
  check("the focus leaves the closed dialog", !(await js("return !!document.activeElement.closest('dialog')")) && (await active()) !== "BODY", await active());
  await toTab("recommended");
  await until("document.querySelectorAll('#list .row[data-i]').length >= 3 && !document.querySelector('#list[aria-busy]')", 8000);
  for (const i of [0, 1]) {                                            // two more Apply clicks: asked newest first
    await js(`document.querySelector('#job-${i}').click()`);
    await until("document.body.classList.contains('sheet-open')");
    await sleep(300);
    await js("[...document.querySelectorAll('#detail .acts .btn')].find((b) => b.textContent.startsWith('Apply')).click()");
    await key("Escape");
    await sleep(350);
  }
  await toTab("tracker");
  await send("Page.bringToFront");
  await js("window.dispatchEvent(new Event('focus'))");
  check("several are asked newest first with 1 of N", await until("document.querySelector('dialog.back-dlg[open]')?.textContent.includes('1 of 2')", 6000), await dialogText());
  check("the dialog still fits", await inside("dialog.back-dlg, dialog.back-dlg .btn"));
  await clickText("dialog.back-dlg .btn", "No");
  check("then the next one", await until("document.querySelector('dialog.back-dlg[open]')?.textContent.includes('2 of 2')"), await dialogText());
  await clickText("dialog.back-dlg .btn", "Yes");
  check("Yes marks applied and attaches the approved resume", await until("document.querySelector('dialog.back-dlg[open]')?.textContent.includes('Tailored resume attached')", 8000), await dialogText());
  check("the form shows only the empty sheet columns, inside the viewport", await js("const f = [...document.querySelectorAll('dialog.back-dlg label.field > span')].map((s) => s.textContent); return f.includes('Target Name') && !f.includes('Company Name')") && await inside("dialog.back-dlg, dialog.back-dlg .btn, dialog.back-dlg input"));
  await shot("phone-did-you-apply-form");
  await tips("Did you apply? form", "dialog.back-dlg[open]");
  await js("[...document.querySelectorAll('dialog.back-dlg label.field')].find((l) => l.textContent.startsWith('Target Name')).querySelector('input').value = 'Pat Doe'");
  await clickText("dialog.back-dlg .btn", "Save");
  await until("!document.querySelector('dialog.back-dlg[open]')");
  const app0 = (await server(`/api/apply_options?profile=${P}&uid=${encodeURIComponent(uids[0])}`)).app;
  const row0 = app0 && await server(`/api/tracker/row?profile=${P}&app=${app0.id}`);
  check("the row is applied with what the form said", row0?.status === "applied" && row0?.target_name === "Pat Doe", JSON.stringify(row0 || {}).slice(0, 160));
  await toTab("recommended");
  check("the list row says Applied without a reload", await until("[...document.querySelectorAll('#job-0 .tag')].some((t) => t.textContent === 'Applied')"),
    await js("return document.querySelector('#job-0 .row-meta')?.textContent"));
  await js("document.querySelector('#job-0').click()");
  check("the sheet's Applied button agrees", await until("document.body.classList.contains('sheet-open') && [...document.querySelectorAll('#detail .acts .btn')].some((b) => b.textContent.includes('Applied'))"),
    await js("return [...document.querySelectorAll('#detail .acts .btn')].map((b) => b.textContent).join(' | ')"));
  await key("Escape");
  await sleep(400);

  // ---- the home-screen app: manifest + worker, the flows that bring the question up, the safe-area CSS ----
  await send("Emulation.setEmulatedMedia", { features: [{ name: "display-mode", value: "standalone" }] });
  await load(`recommended:${P}`);
  // the Later one is on the list the app opens on: asked on its row, else by the dialog
  check("opening the app asks the Later one again", await until("document.querySelector('dialog.back-dlg[open], #list .ask-in')", 8000), await dialogText());
  await shot("standalone-open-asks");
  if (await js("return !!document.querySelector('#list .ask-in')")) await clickText("#list .ask-in .mini", "Not yet");
  else await clickText("dialog.back-dlg .btn", "No");
  await until("!document.querySelector('dialog.back-dlg[open], #list .ask-in')");
  check("the manifest and the worker still serve", await js("const m = await fetch('/manifest.webmanifest'); const w = await fetch('/sw.js'); return m.ok && m.headers.get('content-type').includes('manifest') && w.ok && w.headers.get('content-type').includes('javascript')"));
  check("the app-shell worker is registered", await js("if (!navigator.serviceWorker) return 'no sw api'; const r = await navigator.serviceWorker.ready; return r.active?.state") === "activated");
  check("the toasts keep clear of the bottom tabs and the sheet's bar", await js("const z = getComputedStyle(document.querySelector('#toasts')).bottom; document.body.classList.add('sheet-open'); const s = getComputedStyle(document.querySelector('#toasts')).bottom; document.body.classList.remove('sheet-open'); return z === '72px' && s === '126px' ? true : z + ' ' + s"));
  await js("document.querySelector('#job-3').click()");
  await until("document.body.classList.contains('sheet-open')");
  await sleep(300);
  await js("[...document.querySelectorAll('#detail .acts .btn')].find((b) => b.textContent.startsWith('Apply')).click()");
  await sleep(300);
  await send("Page.bringToFront");
  await js("window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))");
  check("pageshow (back from Safari) asks at the top of the sheet, inside the viewport", await until("document.querySelector('#detail .d-head .ask-in')", 6000)
    && await inside("#detail .d-head .ask-in .mini"));
  await shot("standalone-ask-on-sheet");
  await clickText("#detail .d-head .ask-in .mini", "Not yet");
  await until("!document.querySelector('#detail .ask-in')");
  await key("Escape");

  // ---- the Resume app offline ----
  await fake("/__down", {});
  await js(`await fetch("/api/resumes?profile=${P}")`);             // GOAL30-app §0: one failed call is not offline yet
  await load(`recommended:${P}`);
  await answerAll();
  await js("document.querySelector('#job-0').click()");
  await until("document.body.classList.contains('sheet-open')");
  await sleep(400);                                                   // the slide-in
  check("offline: the phone sheet says so and its bar still fits", await until("document.querySelector('#detail .rg-off')?.textContent.includes('Resume app is offline')") && await inside("#detail .acts > *, #detail .acts .acts-icons > *"),
    JSON.stringify(await boxes("#detail .acts > *")));
  await tips("offline phone sheet (grab + sticky bar)", "#detail .grab, #detail .acts");
  await shot("phone-offline-sheet");
  await desktop();
  await load(`recommended:${P}`);
  await answerAll();
  check("offline: job-hunt-agent keeps working at 1440", (await js("return document.querySelectorAll('#list .row[data-i]').length")) >= 5 && await until("document.querySelector('#detail .rg-off')", 8000));
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || err);
} finally {
  console.log(JSON.stringify({ checks }));
  close();
}

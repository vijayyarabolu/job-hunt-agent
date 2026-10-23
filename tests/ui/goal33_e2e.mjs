// GOAL33 §5: the user's daily flow, end to end, on copies of both apps' data: Recommended -> a job posted in the last 24 h ->
// Generate (Queued at once) -> the job in the Resume app and in Tailored resumes -> the worker finishes it (played by
// tests/e2e_worker.py with an earlier run's files) -> Needs you, live -> Accept as it is -> Ready -> the real Autofill
// extension on Workday fixture pages served at that job's own address: the tailored PDF, the user's work history with the
// employers' countries, the user's school's place, the user's EEO answers -> Mark applied -> an Applied card.
// usage: node goal33_e2e.mjs <job-hunt-agent url> <token> <person> <resumegen url> <resumegen token> <chrome> <profile dir>
//        <extension dir> <fixtures dir> <worker command json> <source job id>
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { launch, serveFixtures, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, PID, RG, RG_TOKEN, CHROME, PROFILE_DIR, EXT_DIR, FIX, WORKER, SOURCE] = process.argv.slice(2);
const LIVE_MS = 2000;                                     // a change in the Resume app is on the page within 2 s
const QUEUED_MS = 300;                                    // Generate says Queued at once
const checks = [];
const timings = {};
const facts = {};
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: typeof detail === "string" ? detail : JSON.stringify(detail) });
const rgGet = async (path) => (await fetch(`${RG}${path}`, { headers: { Authorization: `Bearer ${RG_TOKEN}` } })).json();
const rgJobs = async () => (await rgGet(`/api/jobs?profile=${PID}`)).jobs || [];
const worker = (id, status) => {
  const [cmd, ...args] = JSON.parse(WORKER);
  const out = spawnSync(cmd, [...args, String(id), SOURCE, status], { encoding: "utf8" });
  return (out.stdout || out.stderr || "").trim();
};

const b = await launch(CHROME, PROFILE_DIR);
let app, t;
try {
  const { id } = await b.send("Extensions.loadUnpacked", { path: EXT_DIR });
  const o = await tab(b, `chrome-extension://${id}/options.html`);
  await o.until("document.getElementById('base')");
  await o.js(`const $ = (i) => document.getElementById(i);
    $("base").value = ${JSON.stringify(BASE)}; $("token").value = ${JSON.stringify(TOKEN)};
    document.querySelector("input[name=profile][value=${PID}]").checked = true; $("test").click()`);
  check("the extension reaches job-hunt-agent as this person", await o.until(`document.getElementById('test-out').textContent.startsWith('Connected as ${PID}')`),
    await o.js("return document.getElementById('test-out').textContent"));

  // ---- Recommended -> 24h -> a Workday job with one place, no resume yet ----
  app = await tab(b);
  await app.send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await app.send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:${PID}` });
  const READY = "document.querySelector('#list .row[id^=job-]') && !document.querySelector('#list[aria-busy]')";
  check("Recommended shows jobs", await app.until(READY, 20000));
  await app.js("[...document.querySelectorAll('#filters button')].find((x) => x.textContent.trim() === '24h')?.click()");
  await sleep(300);
  check("24h shows jobs", await app.until(READY, 10000));
  await app.until("document.body.classList.contains('rg-on')", 10000);     // the resume chips are in: skip jobs with one
  const job = await app.js(`const { S } = await import("/js/core.js");
    for (const [i, j] of S.items.entries()) {
      if (j.app || j.resumes?.length || S.rg?.jobs?.[j.uid]) continue;    // tracked, or a resume made already
      const url = j.apply_url || j.url || "";
      if (/\\.myworkdayjobs\\.com\\/.+\\/job\\//.test(url) && /,/.test(j.location || "") && !/remote|;|\\bor\\b|multiple|locations/i.test(j.location || ""))
        return { uid: j.uid, row: "job-" + i, company: j.company, title: j.title, url: url.split("?")[0], location: j.location };
    } return null`);
  check("a 24h Workday job to apply to", job, job);
  if (!job) throw new Error("no 24h Workday job");
  facts.job = job;

  // ---- Generate: Queued at once, then in the Resume app and Tailored resumes ----
  await app.js(`document.getElementById(${JSON.stringify(job.row)}).querySelector('.row-title').click()`);
  check("the job opens", await app.until("document.querySelector('#detail button.rg-gen-label')", 8000));
  const before = new Set((await rgJobs()).map((j) => j.id));
  let t0 = Date.now();
  // measured in the page, frame by frame (a CDP poll from here adds 120 ms a round)
  timings.generate_to_queued_ms = await app.js(`return new Promise((ok) => { const t = performance.now(), on = () => /queued/i.test(document.querySelector('#${job.row} .rgc')?.textContent || "");
    document.querySelector('#detail button.rg-gen-label').click();
    const tick = () => (on() ? ok(Math.round(performance.now() - t)) : performance.now() - t > 3000 ? ok(null) : requestAnimationFrame(tick)); tick(); })`);
  const queued = timings.generate_to_queued_ms != null;
  check("Generate shows Queued at once", queued && timings.generate_to_queued_ms <= QUEUED_MS, timings);
  let made = null;
  for (let i = 0; i < 100 && !made; i++) { made = (await rgJobs()).find((j) => !before.has(j.id) && j.company === job.company) || null; if (!made) await sleep(100); }
  timings.generate_to_resume_app_ms = Date.now() - t0;
  check("the job is in the Resume app's queue", made && made.status === "queued", made);
  if (!made) throw new Error("not in the Resume app");
  facts.rg_id = made.id;

  await app.js("document.querySelector('#tabs [data-tab=resumes]').click()");
  await sleep(300);
  const view = (name) => app.js(`[...document.querySelectorAll('#list-head button')].find((x) => x.textContent.startsWith(${JSON.stringify(name)}))?.click()`);
  const listed = (sel) => `[...document.querySelectorAll('#list .row')].some((r) => r.innerText.includes(${JSON.stringify(job.company)}) && ${sel})`;
  await app.until("[...document.querySelectorAll('#list-head button')].some((x) => x.textContent.startsWith('Queue'))", 8000);
  await view("Queue");
  check("Tailored resumes' Queue lists it", await app.until(listed("true"), LIVE_MS + 3000));

  // ---- the worker finishes it: Needs you reaches the page by itself (no reload) ----
  await app.js("window.__noReload = 1");
  t0 = Date.now();
  facts.worker = worker(made.id, "needs_you");
  const counted = await app.until("Number((document.querySelector('#list-head')?.innerText.match(/Ready\\D*(\\d+)/) || [])[1]) > 0", 5000);
  timings.needs_you_to_page_ms = Date.now() - t0;
  await view("Ready");
  const shown = await app.until(listed("/needs you/i.test(r.innerText)"), LIVE_MS);
  check("Needs you reaches Tailored resumes within 2 s, no reload",
    counted && shown && timings.needs_you_to_page_ms <= LIVE_MS && await app.js("return window.__noReload === 1"), timings);

  // ---- Accept as it is -> Ready ----
  await app.js(`[...document.querySelectorAll('#list .row')].find((r) => r.innerText.includes(${JSON.stringify(job.company)})).querySelector('.row-title').click()`);
  const chip = "[...document.querySelectorAll('#detail button, #detail a')].find((x) => x.textContent.trim() === 'Resume needs you')";
  check("the job shows Resume needs you", await app.until(chip, 8000));
  await app.js(`${chip}.click()`);
  const accept = "[...document.querySelectorAll('#detail button')].find((x) => x.textContent.trim() === 'Accept as it is')";
  check("the resume panel offers Accept as it is", await app.until(accept, 15000), await app.js("return document.querySelector('#detail')?.innerText.slice(0, 600)"));
  await app.js(`${accept}.click()`);
  let accepted = false;
  for (let i = 0; i < 50 && !accepted; i++) { accepted = (await rgJobs()).find((j) => j.id === made.id)?.status === "ready"; if (!accepted) await sleep(200); }
  check("accepted: Ready in the Resume app", accepted);
  check("accepted: Ready in job-hunt-agent", await app.until("/resume ready/i.test(document.querySelector('#detail')?.innerText || '')", LIVE_MS + 3000),
    await app.js("return document.querySelector('#detail')?.innerText.slice(0, 300)"));

  // ---- Autofill on Workday fixture pages at this job's own address ----
  const host = new URL(job.url).hostname;
  const JOB = job.url, APPLY = `${job.url}/apply/applyManually`;
  t = await tab(b);
  await t.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  await serveFixtures(t, b, [host], (u) => {
    if (u.pathname === "/__sim/workday_sim.js") return { file: join(FIX, "workday_sim.js"), type: "text/javascript" };
    if (u.pathname.startsWith("/__sim/steps/")) return { file: join(FIX, "workday", "steps", u.pathname.split("/").pop()), type: "text/html" };
    return { file: join(FIX, "workday", `${u.searchParams.get("fx") || "job"}.html`), type: "text/html" };
  });
  const go = async (url) => { await t.send("Page.navigate", { url }); await t.until("document.readyState === 'complete' && window.__wd && window.__wd.loaded", 10000); };
  const panelHas = async (text, ms = 10000) => { for (let i = 0; i < ms / 200; i++) { if ((await t.panelText()).includes(text)) return true; await sleep(200); } return false; };
  const click = async (text, ms = 10000) => { for (let i = 0; i < ms / 200; i++) { if (await t.panelClick(text)) return true; await sleep(200); } return false; };
  const btnText = (field) => t.js(`return document.querySelector('[data-automation-id="formField-${field}"] button[aria-haspopup]')?.textContent ?? null`);
  const untrusted = () => t.js("return window.__wd.clicks.filter((c) => !c.trusted).map((c) => c.id)");

  await go(`${JOB}?fx=job`);
  check("the panel shows this job", await panelHas(job.company, 15000), await t.panelText());
  check("the panel shows the tailored resume", await panelHas("Tailored resume"), await t.panelText());
  await click("Autofill");                                 // the user presses Autofill once on the posting
  check("the posting says click Apply", await panelHas("Click Apply on the posting"), await t.panelText());
  await sleep(1500);

  await go(`${APPLY}?fx=flow&step=info`);
  // a city with no saved address waits for its first OpenStreetMap street (autofill/address.py LOOKUP_WAIT_S, by design
  // since 10-15; then saved): the test opens the form seconds after the posting, the user usually reads it first
  check("My Information fills on its own", await t.until("document.getElementById('phoneNumber--phoneNumber')?.value", 45000), await t.panelText());
  facts.info = await t.js("return [...document.querySelectorAll('[data-automation-id=applyFlowMyInfoPage] input, [data-automation-id=applyFlowMyInfoPage] button[aria-haspopup]')].map((i) => (i.id || i.name || i.getAttribute('aria-label')) + '=' + (i.type === 'radio' ? i.checked : (i.value || i.textContent)).toString().slice(0, 30))");
  facts.info_panel = (await t.panelText()).slice(0, 3000);

  const mine = await app.js(`return (await (await fetch('/api/autofill/profile?' + new URLSearchParams({ profile: ${JSON.stringify(PID)}, uid: ${JSON.stringify(job.uid)} }))).json()).jobs
    .map((j) => [j.title, j.company, j.country])`);
  facts.resume_jobs = mine;
  const rowsNow = "[...document.querySelectorAll('[data-sec=work] > [role=group]')].map((p) => [p.querySelector('[id$=jobTitle]')?.value, p.querySelector('[id$=companyName]')?.value, p.querySelector('[id$=location]')?.value])";
  await t.clickSel("[data-automation-id=bottom-navigation-next-button]");
  check("My Experience: the user's jobs, in order, each with its employer's country", await t.until(`window.__wd.parsed && JSON.stringify(${rowsNow}) === ${JSON.stringify(JSON.stringify(mine))}`, 90000),
    { page: await t.js(`return ${rowsNow}`), resume: mine });
  const uploaded = await t.js("return window.__wd.uploaded");
  facts.uploaded = uploaded;
  const pdf = await app.js(`const r = await fetch('/api/resumes/file?' + new URLSearchParams({ profile: ${JSON.stringify(PID)}, id: ${made.id}, which: "pdf" }));
    return r.ok ? (await r.blob()).size : null`);
  check("this job's tailored PDF was uploaded", uploaded && uploaded.type === "application/pdf" && uploaded.size === pdf, { uploaded, tailored_pdf_bytes: pdf });
  check("no work field holds the user's home", !(await t.js("return [...document.querySelectorAll('[data-sec=work] input')].some((i) => /Austin|66223/.test(i.value))")));

  await go(`${APPLY}?fx=flow&step=experience_nvidia`);
  await click("Autofill");
  await t.until("document.querySelector('[data-sec=education] input[id$=\"--location\"]')?.value", 60000);
  facts.school = await t.js("return document.querySelector('[data-sec=education] input[id$=\"--location\"]')?.value ?? null");
  check("the school's place is Springfield, IL", facts.school === "Springfield, IL", facts.school);

  await go(`${APPLY}?fx=flow&step=disclosures_nvidia`);
  await click("Autofill");
  await t.until("!/Select One/.test(document.querySelector('[data-automation-id=\"formField-veteranStatus\"] button')?.textContent || 'Select One')", 30000);
  await sleep(2000);
  facts.eeo = { gender: await btnText("gender"), ethnicity: await btnText("ethnicity"), veteran: await btnText("veteranStatus") };
  check("EEO from the user's saved answers", Object.values(facts.eeo).every((v) => v && !/Select One/.test(v)), facts.eeo);
  check("Autofill never pressed Next or Submit", (await untrusted()).every((x) => !/next|submit/i.test(x)), await untrusted());

  // ---- Mark applied -> Applied card ----
  check("Mark applied in the panel", await click("Mark applied"));
  check("the panel says it is marked Applied", (await panelHas("marked Applied in Job Hunt Agent", 10000)) || (await panelHas("Already marked applied", 1000)), await t.panelText());
  await app.js("document.querySelector('#tabs [data-tab=applied]').click()");
  check("Applied shows the card", await app.until(`[...document.querySelectorAll('#list .row')].some((r) => r.innerText.includes(${JSON.stringify(job.company)}))`, 15000),
    await app.js("return [...document.querySelectorAll('#list .row')].slice(0, 3).map((r) => r.innerText.slice(0, 80))"));
  await app.js(`[...document.querySelectorAll('#list .row')].find((r) => r.innerText.includes(${JSON.stringify(job.company)}))?.click()`);
  const meta = await app.until("/Applied [A-Z][a-z]{2} \\d{2}/.test(document.querySelector('#detail .meta, #detail')?.innerText || '')", 8000)
    && await app.js("return document.querySelector('#detail').innerText.slice(0, 400)");
  check("its pane says when the user applied, not that the user applied before", !!meta && !meta.includes("(applied before)"), meta);
  facts.page_errors = [...app.errors, ...t.errors];
} catch (err) {
  check("runner", false, String(err?.stack || err));
} finally {
  b.close();
}
console.log(JSON.stringify({ checks, timings, facts }));

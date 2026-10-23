// GOAL31 §2, the real Autofill extension in headless Chrome against a real job-hunt-agent server, on EEO sections with the
// choices the user's boards really showed: Workday (NVIDIA's and 4 other tenants' exact lists, a list still loading when it
// opens, a first click that opens nothing, the last list slow to go away), the real Ashby page (its EEO survey is a
// second form), Lever and Breezy. Every EEO answer is read back from the page. Then "Send page snapshot" on Ashby.
// usage: node goal31_eeo.mjs <base> <token> <chrome> <profile dir> <extension dir> <fixtures dir>
import { join } from "node:path";
import { launch, serveFixtures, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXT_DIR, FIX] = process.argv.slice(2);
const HOST = "zztest.wd5.myworkdayjobs.com";
const APPLY = `https://${HOST}/en-US/External/job/Foster-City-CA/AI-Engineer_JR0001/apply/applyManually`;
const BOARDS = { ashby: { host: "jobs.ashbyhq.com", path: "/zztest/31001/application", file: "ashby_unify.html" },
  lever: { host: "jobs.lever.co", path: "/zztest/31002/apply", file: "lever.html" },
  breezy: { host: "zztest.breezy.hr", path: "/p/31003-ml-engineer/apply", file: "breezy.html" } };
const RACE_WD = "Asian (not Hispanic or Latino) (United States of America)";
const checks = [];
const timings = {};
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: typeof detail === "string" ? detail : JSON.stringify(detail) });

const b = await launch(CHROME, PROFILE_DIR);
let t;
try {
  const { id } = await b.send("Extensions.loadUnpacked", { path: EXT_DIR });
  const o = await tab(b, `chrome-extension://${id}/options.html`);
  await o.until("document.getElementById('base')");
  await o.js(`const $ = (i) => document.getElementById(i);
    $("base").value = ${JSON.stringify(BASE)}; $("token").value = ${JSON.stringify(TOKEN)};
    document.querySelector("input[name=profile][value=zztest]") || document.querySelector(".seg").append(Object.assign(document.createElement("input"), {type: "radio", name: "profile", value: "zztest"}));
    document.querySelector("input[name=profile][value=zztest]").checked = true; $("test").click()`);
  check("the extension reaches job-hunt-agent", await o.until("document.getElementById('test-out').textContent.startsWith('Connected as zztest')"),
    await o.js("return document.getElementById('test-out').textContent"));

  t = await tab(b);
  await t.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false });
  await serveFixtures(t, b, [HOST, ...Object.values(BOARDS).map((x) => x.host)], (u) => {
    const board = Object.values(BOARDS).find((x) => x.host === u.hostname);
    if (board) return { file: join(FIX, "goal31", board.file), type: "text/html" };
    if (u.pathname === "/__sim/workday_sim.js") return { file: join(FIX, "workday_sim.js"), type: "text/javascript" };
    if (u.pathname.startsWith("/__sim/steps/")) return { file: join(FIX, "workday", "steps", u.pathname.split("/").pop()), type: "text/html" };
    return { file: join(FIX, "workday", `${u.searchParams.get("fx") || "job"}.html`), type: "text/html" };
  });
  const click = async (text, ms = 10000) => {             // the panel draws its buttons a moment after the page
    for (let i = 0; i < ms / 200; i++) { if (await t.panelClick(text)) return true; await sleep(200); }
    return false;
  };
  const reported = async () => {                          // the panel's counts come after job-hunt-agent has the per-field log
    for (let i = 0; i < 50; i++) { if (/Filled \d+ · Needs you \d+/.test(await t.panelText())) return true; await sleep(200); }
    return false;
  };
  const autofill = async (done, ms) => {                  // a busy machine: an auto-fill already running ignores a press
    for (let i = 0; i < 3; i++) {
      if (!(await click("Autofill"))) continue;
      if (await t.until(done, ms)) return reported();
    }
    return false;
  };

  // ---- Workday: open, wait for the choices, then pick; a list that does not open is opened again (3 times) ----
  const btn = (field) => t.js(`return document.querySelector('[data-automation-id="formField-${field}"] button[aria-haspopup]')?.textContent ?? null`);
  const workday = async (name, step, query, want) => {
    await t.send("Page.navigate", { url: `${APPLY}?fx=flow&step=${step}${query}` });
    await t.until("document.readyState === 'complete' && window.__wd && window.__wd.loaded", 10000);
    const t0 = Date.now();
    const last = Object.keys(want).map((f) => `document.querySelector('[data-automation-id="formField-${f}"] button')?.textContent !== 'Select One'`).join(" && ");
    await autofill(`(${last}) && document.querySelector('[data-automation-id=agreementCheckbox]')?.checked`, 45000);
    timings[`workday_${name}_ms`] = Date.now() - t0;
    const got = {};
    for (const f of Object.keys(want)) got[f] = await btn(f);
    check(`workday ${name}: every EEO answer picked and shown`, Object.keys(want).every((f) => got[f] === want[f]), { got, want });
  };
  const NVIDIA = { ethnicity: RACE_WD, gender: "Male", veteranStatus: "I AM NOT A VETERAN" };
  await workday("NVIDIA lists", "disclosures_real", "", NVIDIA);
  await workday("NVIDIA lists, last list slow to go away", "disclosures_real", "&stale=1", NVIDIA);
  await workday("NVIDIA lists, each list loading 3.5 s on its first open", "disclosures_real", "&late=3500", NVIDIA);
  await workday("NVIDIA lists, first click opens nothing", "disclosures_real", "&deaf=1", NVIDIA);
  await workday("other tenants' wording, Hispanic on its own", "disclosures_tenants", "&deaf=1&late=1500",
    { gender: "Male", hispanic: "No", ethnicity: RACE_WD, veteranStatus: "I AM NOT A VETERAN" });

  // ---- the other boards ----
  const board = async (name, done) => {
    await t.send("Page.navigate", { url: `https://${BOARDS[name].host}${BOARDS[name].path}` });
    await t.until("document.readyState === 'complete'", 10000);
    return autofill(done, 15000);
  };
  const picked = (path) => t.js(`const r = document.querySelector('input[type=radio][name$="${path}"]:checked');
    return r ? (document.querySelector('label[for="' + CSS.escape(r.id) + '"]')?.textContent ?? '?') : ''`);
  await board("ashby", "document.querySelector('input[name$=_systemfield_eeoc_veteran_status]:checked')");
  const ashby = { gender: await picked("_systemfield_eeoc_gender"), race: await picked("_systemfield_eeoc_race"),
    veteran: await picked("_systemfield_eeoc_veteran_status") };
  check("ashby (real page): the EEO survey's radios are found and filled", ashby.gender === "Male" && ashby.race === "Asian (Not Hispanic or Latino)" &&
    ashby.veteran === "I am not a protected veteran", ashby);
  const panel = await t.panelText();
  check("ashby: the panel names the EEO questions, never the whole form as one label", /Gender/.test(panel) && !/Full NamePreferred Name/.test(panel), panel.slice(0, 1500));

  // "Send page snapshot" keeps the page in job-hunt-agent (autofill_snapshots, read by the Python side) and says so
  const snap = async (name) => {
    check(`${name}: the button is there`, await click("Send page snapshot"));
    check(`${name}: the panel says it was kept`, await (async () => {
      for (let i = 0; i < 50; i++) { if (/Page snapshot (sent|kept)/.test(await t.panelText())) return true; await sleep(200); }
      return false;
    })(), (await t.panelText()).slice(-600));
  };
  await snap("send page snapshot");
  // a page whose HTML grows past 4 MB once JSON-escaped (every backslash doubles): cut to fit, still kept (the Python
  // side counts the rows: the panel already shows the first one's note)
  await t.js("document.body.append(Object.assign(document.createElement('div'), { hidden: true, textContent: '\\\\'.repeat(2500000) })); return 1");
  check("send page snapshot of a big page: the button is there", await click("Send page snapshot"));
  await sleep(3000);

  const v = (sel) => t.js(`const s = document.querySelector(${JSON.stringify(sel)}); return s ? s.options[s.selectedIndex]?.textContent ?? '' : null`);
  await board("lever", "document.querySelector('select[name=\"eeo[disability]\"]').value");
  const lever = { gender: await v("select[name='eeo[gender]']"), race: await v("select[name='eeo[race]']"),
    veteran: await v("select[name='eeo[veteran]']"), disability: await v("select[name='eeo[disability]']") };
  check("lever: every EEO answer", lever.gender === "Male" && lever.race === "Asian (Not Hispanic or Latino)" && lever.veteran === "I am not a protected veteran" &&
    lever.disability === "No, I do not have a disability", lever);

  await board("breezy", "document.getElementById('eeo_disability').value");
  const breezy = { gender: await v("#eeo_gender"), race: await v("#eeo_race"), veteran: await v("#eeo_veteran"), disability: await v("#eeo_disability") };
  check("breezy: every EEO answer", breezy.gender === "Male" && breezy.race === "Asian (not Hispanic or Latino)" && breezy.veteran === "I am not a protected veteran" &&
    breezy.disability === "No, I do not have a disability", breezy);
} catch (err) {
  check("runner", false, String((err && err.stack) || err));
} finally {
  if (t) { timings.page_errors = t.errors.slice(0, 20); timings.page_logs = t.logs.slice(0, 40); }
  await b.close();
}
console.log(JSON.stringify({ checks, timings }));

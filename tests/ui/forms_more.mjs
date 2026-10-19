// 1.2.1 (user 2026-10-14): the real extension on forms with the sites' exact labels: Paylocity's address and cover
// letter, Ashby's city typeahead, Breezy's Add Position / Add Education repeaters, a site no board script knows (its
// layout learned for the next visit) and Workday's My Experience. Driven by tests/test_forms_more.py.
// usage: node forms_more.mjs <base> <token> <chrome> <profile dir> <extension dir> <fixtures dir> <pages json> <workday job url> <expected json>
import { join } from "node:path";
import { launch, serveFixtures, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXT_DIR, FIX, PAGES_JSON, WD_JOB, WANT_JSON] = process.argv.slice(2);
const [PAY, ASH, BRZ, UNK] = JSON.parse(PAGES_JSON);
const WANT = JSON.parse(WANT_JSON);
const WD = `${WD_JOB}/apply/applyManually`;
const host = (u) => new URL(u).hostname;
const FORMS = { [host(PAY)]: "paylocity_address.html", [host(ASH)]: "ashby_city.html", [host(BRZ)]: "breezy_experience.html", [host(UNK)]: "unknown_experience.html" };
const checks = [];
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
    document.querySelector("input[name=profile][value=zztest]").checked = true;
    $("email").value = "zz.testcase@example.com"; $("password").value = "Zz-Test-Pass-1";
    $("form").requestSubmit(); await new Promise((r) => setTimeout(r, 300)); return true`);

  t = await tab(b);
  await serveFixtures(t, b, [...Object.keys(FORMS), host(WD)], (u) => {
    if (FORMS[u.hostname]) return { file: join(FIX, "forms", FORMS[u.hostname]), type: "text/html" };
    if (u.pathname === "/__sim/workday_sim.js") return { file: join(FIX, "workday_sim.js"), type: "text/javascript" };
    if (u.pathname.startsWith("/__sim/steps/")) return { file: join(FIX, "workday", "steps", u.pathname.split("/").pop()), type: "text/html" };
    return { file: join(FIX, "workday", `${u.searchParams.get("fx") || "job"}.html`), type: "text/html" };
  });
  const go = async (url, ready = "document.readyState === 'complete'") => { await t.send("Page.navigate", { url }); await t.until(ready, 15000); await sleep(1500); };
  const panelHas = async (text, ms = 10000) => { for (let i = 0; i < ms / 200; i++) { if ((await t.panelText()).includes(text)) return true; await sleep(200); } return false; };
  const val = (sel) => t.js(`return document.querySelector(${JSON.stringify(sel)})?.value ?? null`);
  const vals = (sels) => Promise.all(sels.map(val));
  const isOn = (sel) => t.js(`return !!document.querySelector(${JSON.stringify(sel)})?.checked`);
  const count = (sel) => t.js(`return document.querySelectorAll(${JSON.stringify(sel)}).length`);
  async function autofill() {                           // click Autofill, wait for the panel's count
    await panelHas("Autofill", 15000);
    await t.panelClick("Autofill");
    await sleep(800);
    for (let i = 0; i < 300; i++) { const s = await t.panelText(); if (s.includes("Filled ") && !s.includes("Filling…")) return s; await sleep(300); }
    return t.panelText();
  }
  const noNeeds = (s) => /Needs you 0\b/.test(s);
  const [j1, j2] = WANT.jobs;

  // ---- Paylocity: every address field from the job's address, the cover letter left empty, nothing asked ----
  await go(PAY);
  let panel = await autofill();
  const pay = await vals(["#addr", "#loc", "#aa", "#ss", "#pc"]);
  check("Paylocity: street, Locality, Administrative Area, StateSelect and Postal Code filled from the address",
    JSON.stringify(pay) === JSON.stringify(["123 Main St", "Springfield", "Missouri", "MO", "65801"]), pay);
  check("Paylocity: a label showing its dropdown's pick is still the address", JSON.stringify(await vals(["#co", "#st2"])) === JSON.stringify(["United States", "Missouri"]) &&
    !/CountryUnited|StateSelect a/.test(panel), [await vals(["#co", "#st2"]), panel]);
  check("Paylocity: the cover letter is left empty", (await val("#cl")) === "", await val("#cl"));
  check("Paylocity: nothing in Needs you", noNeeds(panel) && (await count("[data-jh-need]")) === 0, panel);

  // ---- Ashby: many Springfields; the address's state in the United States ----
  await go(ASH);
  await autofill();
  check("Ashby: the Springfield in Missouri, United States", await t.until("document.getElementById('city').dataset.picked === 'Springfield, Missouri, United States'", 15000),
    [await val("#city"), await t.js("return document.getElementById('city').dataset.picked || ''")]);

  // ---- Breezy: Add Position / Add Education, the resume's jobs in order, bullets, summary ----
  await go(BRZ);
  panel = await autofill();
  const titles = await vals(["#title1", "#company1", "#title2", "#company2"]);
  check("Breezy: one position per resume job, in order", (await count(".position")) === 2 &&
    JSON.stringify(titles) === JSON.stringify([j1.title, j1.company, j2.title, j2.company]), titles);
  const dates = [...(await vals(["#sm1", "#sy1", "#em1", "#ey1", "#sm2", "#sy2", "#em2", "#ey2"])), await isOn("#current1"), await isOn("#current2")];
  check("Breezy: dates and I currently work here", JSON.stringify(dates) === JSON.stringify(["3", "2023", "", "", "6", "2020", "2", "2023", true, false]), dates);
  const descs = await vals(["#psum1", "#psum2"]);
  check("Breezy: each position's bullets, one \"• \" line each", JSON.stringify(descs) === JSON.stringify([j1.description, j2.description]) &&
    descs[0] === "• Built retrieval pipelines in Python.\n• Cut model latency 40% with quantization." && descs[1] === "• Shipped fraud models.\n• Ran A/B tests.", descs);
  const edu = await vals(["#school1", "#degree1", "#field1", "#esm1", "#esy1", "#eem1", "#eey1"]);
  check("Breezy: education without a start date", (await count(".school")) === 1 &&
    JSON.stringify(edu) === JSON.stringify(["University of Central Missouri", "Master's Degree", "Computer Science", "", "", "5", "2024"]), edu);
  check("Breezy: the Summary box is the resume's summary", (await val("#summary")) === WANT.summary, await val("#summary"));
  check("Breezy: the cover letter is left empty", (await val("#cover")) === "", await val("#cover"));
  await autofill();
  check("Breezy: a second Autofill adds nothing", (await count(".position")) === 2 && (await count(".school")) === 1 &&
    JSON.stringify(await vals(["#title1", "#title2", "#psum1"])) === JSON.stringify([j1.title, j2.title, j1.description]), [await count(".position"), await count(".school")]);
  check("Breezy: nothing in Needs you", noNeeds(await t.panelText()) && (await count("[data-jh-need]")) === 0,
    await t.panelText());

  // ---- a site nothing knows: its own words, an entry it parsed wrong, its layout learned ----
  const unknownChecks = async (prefix) => {
    const rows = await vals(["#emp1", "#jt1", "#city1", "#emp2", "#jt2"]);
    const okRows = (await count("fieldset.employer")) === 2 && JSON.stringify(rows) === JSON.stringify([j1.company, j1.title, j1.location, j2.company, j2.title]);
    const when = [...(await vals(["#from1", "#to1", "#from2", "#to2"])), await isOn("#still1"), await isOn("#still2")];
    const okWhen = JSON.stringify(when) === JSON.stringify(["03/2023", "", "06/2020", "02/2023", true, false]);
    const resp = await vals(["#resp1", "#resp2"]);
    const school = await vals(["#inst1", "#qual1", "#major1", "#gd1", "#sd1"]);
    const okSchool = (await count("fieldset.school")) === 1 && JSON.stringify(school) === JSON.stringify(["University of Central Missouri", "Master's Degree", "Computer Science", "2024-05", ""]);
    return { okRows, rows, okWhen, when, okResp: JSON.stringify(resp) === JSON.stringify([j1.description, j2.description]), resp, okSchool, school,
      okAbout: (await val("#about")) === WANT.summary, about: await val("#about"), prefix };
  };
  await go(UNK);
  await autofill();
  let u = await unknownChecks("first");
  check("unknown site: the entry it parsed is corrected, one added, in order", u.okRows, u.rows);
  check("unknown site: MM/YYYY dates and Still employed here", u.okWhen, u.when);
  check("unknown site: Responsibilities are the bullets", u.okResp, u.resp);
  check("unknown site: education by its own words, graduation month, no start date", u.okSchool, u.school);
  check("unknown site: About you is the summary", u.okAbout, u.about);
  const layout = await o.js(`return ((await chrome.storage.local.get("jh.layouts"))["jh.layouts"] || {})[${JSON.stringify(host(UNK))}] || null`);
  check("unknown site: its layout is remembered", layout && layout.exp && layout.exp.heading === "employment history" && layout.exp.add === "add employer" &&
    layout.exp.roles["responsibilities"] === "desc" && layout.edu && layout.edu.add === "add school" && layout.edu.roles["graduation date"] === "end", layout);
  await go(UNK);
  await autofill();
  u = await unknownChecks("second");
  check("unknown site: the next visit fills it the same way", u.okRows && u.okWhen && u.okResp && u.okSchool && u.okAbout, u);

  // ---- Workday's My Experience: the resume's jobs in order, each Role Description its bullets ----
  await go(`${WD}?fx=flow&step=experience`, "document.readyState === 'complete' && window.__wd && window.__wd.loaded");
  await autofill();
  const wdOk = await t.until(`document.querySelectorAll('[data-sec=work] > [role=group]').length === 2 && document.getElementById('workExperience-2--jobTitle')?.value === ${JSON.stringify(j2.title)}`, 90000);
  const wd = await t.js("return [...document.querySelectorAll('[data-sec=work] > [role=group]')].map((g) => [g.querySelector('input[id$=jobTitle]')?.value, g.querySelector('input[id$=companyName]')?.value, g.querySelector('[id$=roleDescription]')?.value])");
  check("Workday: the resume's jobs in order", wdOk && JSON.stringify(wd.map((r) => r.slice(0, 2))) === JSON.stringify([[j1.title, j1.company], [j2.title, j2.company]]), wd);
  check("Workday: Role Description is the job's bullets", JSON.stringify(wd.map((r) => r[2])) === JSON.stringify([j1.description, j2.description]), wd);
  check("no page errors", t.errors.length === 0, t.errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
} finally {
  if (process.env.JH_DEBUG && t) {
    const { writeFileSync } = await import("node:fs");
    writeFileSync(process.env.JH_DEBUG, JSON.stringify({ logs: t.logs, panel: await t.panelText().catch(() => "") }, null, 1));
  }
  b.close();
  console.log(JSON.stringify({ checks }));
}

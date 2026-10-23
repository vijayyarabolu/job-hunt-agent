// GOAL27: the adapter matrix. The real extension in headless Chrome, a fake person on a throwaway job-hunt-agent, one Autofill
// click on each ATS's apply page (tests/fixtures/autofill/goal27/sites, served at that ATS's own address so its
// adapter runs) and on the Workday simulator's steps. Every control carrying data-expect is read back as a person
// sees it; the result is one row per site and one column per field type (pass / fail), plus the safety checks: no
// Submit / Next clicked, sensitive fields empty, the panel's marks. Driven by tests/test_goal27_matrix.py.
// usage: node goal27_matrix.mjs <base> <token> <chrome> <profile dir> <extension dir> <fixtures dir> <pages json> <workday job url>
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { launch, serveFixtures, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXT_DIR, FIX, PAGES_JSON, WD_JOB] = process.argv.slice(2);
const PAGES = JSON.parse(PAGES_JSON);
const SITES = join(FIX, "goal27", "sites");
const rows = [];
const checks = [];
// JH_SHOTS=<dir>: the panel after Autofill on Greenhouse and Workday, as PNGs (before / after screenshots for a PR)
const shot = async (tb, name) => {
  if (!process.env.JH_SHOTS) return;
  const { writeFileSync } = await import("node:fs");
  const { data } = await tb.send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(process.env.JH_SHOTS, `${name}.png`), Buffer.from(data, "base64"));
};
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: typeof detail === "string" ? detail : JSON.stringify(detail) });
const norm = (s) => String(s ?? "").toLowerCase().replace(/\s+/g, " ").trim();
function same(actual, expect) {
  if (expect === "") return norm(actual) === "";
  if (expect.startsWith("re:")) return new RegExp(expect.slice(3)).test(String(actual ?? ""));
  return norm(actual) === norm(expect) || (norm(expect).length > 3 && norm(actual).includes(norm(expect)));
}

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
  // the extension's own world in the page (the latest one: each navigation makes a new one)
  const mainFrame = (await t.send("Page.getFrameTree")).frameTree.frame.id;
  let extCtx = null;
  b.on((m) => {
    if (m.sessionId !== t.sessionId || m.method !== "Runtime.executionContextCreated") return;
    const c = m.params.context;
    if (c.name === "Job Hunt Agent Autofill" && c.auxData && c.auxData.frameId === mainFrame && c.auxData.type === "isolated") extCtx = c.id;
  });
  const ext = async (expr) => (await t.send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, contextId: extCtx, awaitPromise: true, returnByValue: true })).result?.value;
  const hosts = [...new Set(PAGES.map((p) => p.host)), new URL(WD_JOB).hostname];
  await serveFixtures(t, b, hosts, (u) => {
    if (u.pathname === "/__sim/ats_sim.js") return { file: join(FIX, "goal27", "ats_sim.js"), type: "text/javascript" };
    if (u.pathname === "/__sim/workday_sim.js") return { file: join(FIX, "workday_sim.js"), type: "text/javascript" };
    if (u.pathname.startsWith("/__sim/steps/")) return { file: join(FIX, "workday", "steps", u.pathname.split("/").pop()), type: "text/html" };
    if (u.pathname.startsWith("/account/")) return { file: join(FIX, "goal27", "auth", `${u.pathname.split("/").pop()}.html`), type: "text/html" };
    if (u.hostname === new URL(WD_JOB).hostname) return { file: join(FIX, "workday", `${u.searchParams.get("fx") || "job"}.html`), type: "text/html" };
    const page = PAGES.find((p) => p.host === u.hostname && (u.pathname === p.path || u.pathname + u.search === p.path)) || PAGES.find((p) => p.host === u.hostname && u.pathname.startsWith(p.path) && p.file.endsWith("_form.html"));
    return page ? { file: join(SITES, page.file), type: "text/html" } : null;
  });
  const go = async (url, ready = "document.readyState === 'complete'") => { await t.send("Page.navigate", { url }); await t.until(ready, 15000); await sleep(1200); };
  const panelHas = async (text, ms = 10000) => { for (let i = 0; i < ms / 200; i++) { if ((await t.panelText()).includes(text)) return true; await sleep(200); } return false; };
  async function autofill() {
    await panelHas("Autofill", 15000);
    await t.panelClick("Autofill");
    await sleep(800);
    for (let i = 0; i < 400; i++) { const s = await t.panelText(); if (/Filled \d+ · Needs you/.test(s) && !s.includes("Filling…")) return s; await sleep(300); }
    return t.panelText();
  }
  // the page's checked controls, read as a person sees them (the frame's own document for iCIMS)
  const readExpect = (frameSel) => t.js(`
    const doc = ${frameSel ? `document.querySelector(${JSON.stringify(frameSel)}).contentDocument` : "document"};
    const win = doc.defaultView;
    const label = (el) => (el.querySelector && (el.querySelector("label, legend, ._label")?.textContent || "")) || el.dataset.label || el.getAttribute("aria-label") || el.id || el.name || "";
    const all = [];
    const walk = (root) => { for (const el of root.querySelectorAll("*")) { if (el.dataset && el.dataset.expect != null) all.push(el); if (el.shadowRoot) walk(el.shadowRoot); } };
    walk(doc);
    return all.map((el) => ({ type: el.dataset.type, expect: el.dataset.expect, actual: win.__simValue(el),
      label: (label(el.closest(".field, .application-question, ._fieldEntry, .iCIMS_InfoField, fieldset, .row") || el) || label(el)).trim().slice(0, 80) }));`);
  const readJobs = (frameSel) => t.js(`
    const doc = ${frameSel ? `document.querySelector(${JSON.stringify(frameSel)}).contentDocument` : "document"};
    if (!doc.querySelector("[data-check-jobs]")) return null;
    const v = (s) => doc.querySelector(s)?.value ?? null, on = (s) => !!doc.querySelector(s)?.checked;
    return { jobs: [...doc.querySelectorAll(".entry.job")].map((e, i) => [v("#title-" + i), v("#company-" + i), v("#sm-" + i), v("#sy-" + i), v("#em-" + i), v("#ey-" + i), on("#cur-" + i), v("#desc-" + i)]),
      schools: [...doc.querySelectorAll(".entry.school")].map((e, i) => [v("#school-" + i), doc.querySelector("#degree-" + i)?.selectedOptions[0]?.textContent, v("#major-" + i), v("#gy-" + i)]) };`);
  const WANT_JOBS = [["Senior ML Engineer", "Globex Corp", "3", "2023", "", "", true, "• Built retrieval pipelines in Python.\n• Cut model latency 40% with quantization."],
    ["ML Engineer", "Initech", "6", "2020", "2", "2023", false, "• Shipped fraud models.\n• Ran A/B tests."]];
  const WANT_SCHOOLS = [["Lakeview State University", "Master's Degree", "Computer Science", "2024"]];

  const only = process.env.JH_ONLY ? new RegExp(process.env.JH_ONLY, "i") : null;     // a debugging run: some sites only
  for (const page of PAGES.filter((p) => !p.file.endsWith("_form.html") && (!only || only.test(p.name)))) {
    const url = `https://${page.host}${page.path}`;
    const frame = page.frame || null;
    await go(url, frame ? `document.readyState === 'complete' && document.querySelector(${JSON.stringify(frame)})?.contentWindow?.__sim?.ready`
      : "document.readyState === 'complete' && window.__sim && window.__sim.ready");
    const panel = await autofill();
    if (page.name === "Greenhouse") {
      await sleep(1000);
      await shot(t, "panel-greenhouse");
      if (process.env.JH_SHOTS) {                        // 1.3.1 #5: the "Saved for next time" rows, full width
        await ext(`JH.panel.set({ learned: [{ id: 1, scope: "global", label: "Will you now or in the future require sponsorship for employment visa status (e.g. H-1B visa status)?", answer: "No" },
          { id: 2, scope: "company", company: "ZZ Test Corp", label: "What is your notice period?", answer: "2 weeks" }] }); return true`);
        await sleep(400);
        await ext(`JH.panel.get(); return true`);
        await t.js("return true");
        await shot(t, "panel-learned");
      }
    }
    await sleep(frame ? 2500 : 600);
    const fields = await readExpect(frame);
    const cells = {};
    for (const f of fields) {
      const ok = same(f.actual, f.expect);
      (cells[f.type] = cells[f.type] || { pass: 0, fail: 0, misses: [] })[ok ? "pass" : "fail"]++;
      if (!ok) cells[f.type].misses.push(`${f.label}: want ${JSON.stringify(f.expect)} got ${JSON.stringify(f.actual)}`);
    }
    const reps = await readJobs(frame);
    if (reps) {
      const okJobs = JSON.stringify(reps.jobs) === JSON.stringify(WANT_JOBS), okSchools = JSON.stringify(reps.schools) === JSON.stringify(WANT_SCHOOLS);
      const c = (cells.repeater = cells.repeater || { pass: 0, fail: 0, misses: [] });
      c[okJobs ? "pass" : "fail"]++; c[okSchools ? "pass" : "fail"]++;
      if (!okJobs) c.misses.push(`work history: ${JSON.stringify(reps.jobs)}`);
      if (!okSchools) c.misses.push(`education: ${JSON.stringify(reps.schools)}`);
    }
    const clicks = await t.js(`const w = ${frame ? `document.querySelector(${JSON.stringify(frame)}).contentWindow` : "window"}; return w.__sim.clicks`);
    const marks = await t.js(`const d = ${frame ? `document.querySelector(${JSON.stringify(frame)}).contentDocument` : "document"}; return [...d.querySelectorAll("[data-jh-mark]")].map((m) => m.dataset.jhMark)`);
    rows.push({ site: page.name, board: page.board, cells, clicks, marks: { filled: marks.filter((m) => m === "filled").length, review: marks.filter((m) => m === "review").length,
      needs: marks.filter((m) => m === "needs").length, failed: marks.filter((m) => m === "failed").length }, panel: panel.slice(0, 400) });
  }

  // ---- Workday: the simulator's steps (account fields are covered by the GOAL23 tests) ----
  if (!only || only.test("workday")) {
  const WD = `${WD_JOB}/apply/applyManually`;
  const wdRead = (map) => t.js(`const out = {}; for (const [k, ff] of Object.entries(${JSON.stringify(map)})) {
      const box = document.querySelector('[data-automation-id="formField-' + ff + '"]');
      const sel = box && [...box.querySelectorAll("[data-automation-id=selectedItem]")].map((x) => x.textContent.trim()).join("; ");
      const radio = box && box.querySelector("input[type=radio]:checked");
      const btn = box && box.querySelector("button[aria-haspopup]");
      const input = box && box.querySelector("input:not([type=radio]):not([type=checkbox]), textarea");
      out[k] = !box ? null : sel || (radio ? (box.querySelector('label[for="' + radio.id + '"]') || radio.parentElement).textContent.trim() : btn ? btn.textContent.trim() : input ? input.value : "");
    } return out;`);
  const wdCells = {};
  const wdCheck = (type, label, actual, expect) => {
    const ok = same(actual, expect);
    (wdCells[type] = wdCells[type] || { pass: 0, fail: 0, misses: [] })[ok ? "pass" : "fail"]++;
    if (!ok) wdCells[type].misses.push(`${label}: want ${JSON.stringify(expect)} got ${JSON.stringify(actual)}`);
  };
  await go(`${WD}?fx=flow&step=info`, "document.readyState === 'complete' && window.__wd && window.__wd.loaded");
  await autofill();
  await sleep(1500);
  await shot(t, "panel-workday");
  const info = await wdRead({ first: "legalName--firstName", last: "legalName--lastName", city: "city", state: "countryRegion", zip: "postalCode",
    phoneType: "phoneType", code: "countryPhoneCode", phone: "phoneNumber", source: "source", prev: "candidateIsPreviousWorker" });
  wdCheck("text", "First Name", info.first, "Zz"); wdCheck("text", "Last Name", info.last, "Testcase"); wdCheck("text", "City", info.city, "Springfield");
  wdCheck("dropdown", "State", info.state, "Missouri"); wdCheck("text", "Postal Code", info.zip, "65801"); wdCheck("dropdown", "Phone Device Type", info.phoneType, "Mobile");
  wdCheck("typeahead", "Country Phone Code", info.code, "United States of America (+1)"); wdCheck("phone", "Phone Number", info.phone, "555-010-0199");
  wdCheck("typeahead", "How Did You Hear", info.source, "Company Website"); wdCheck("radio", "Previously employed", info.prev, "No");
  await go(`${WD}?fx=flow&step=questions1`, "document.readyState === 'complete' && window.__wd && window.__wd.loaded");
  await autofill();
  await sleep(1500);
  const q = await wdRead({ auth: "q1", spons: "q2", age: "q3", start: "availableDate", when: "qStart" });
  wdCheck("legal", "Authorized", q.auth, "Yes"); wdCheck("legal", "Sponsorship", q.spons, "No"); wdCheck("legal", "18 or older", q.age, "Yes");
  wdCheck("dropdown", "When can you start", q.when, "Within 2 weeks");
  const wdDate = await t.js(`const box = document.querySelector('[data-automation-id="formField-availableDate"]'); return box ? [...box.querySelectorAll("input")].map((i) => i.value).join("/") : null`);
  wdCheck("date", "Earliest start date", wdDate, "re:^\\d{2}/\\d{2}/\\d{4}$");
  // 1.3.1 #3 / #4: a pre-selected "Yes" for the veteran question is corrected; choices read by meaning; Claude picks
  // from the list when no wording matches
  await go(`${WD}?fx=flow&step=disclosures_c1`, "document.readyState === 'complete' && window.__wd && window.__wd.loaded");
  await autofill();
  await sleep(1200);
  const dc = await wdRead({ vet: "veteranStatusYN", gender: "gender", hisp: "hispanicOrLatino", race: "ethnicity", dis: "disabilityYN", gid: "genderIdentity" });
  wdCheck("eeo", "Are you a protected Veteran? (pre-selected Yes)", dc.vet, "No");
  wdCheck("eeo", "Gender (Man / Woman)", dc.gender, "Man");
  wdCheck("eeo", "Hispanic or Latino", dc.hisp, "No");
  wdCheck("eeo", "Race/Ethnicity (Not Hispanic or Latino) wording", dc.race, "Asian (Not Hispanic or Latino) (United States of America)");
  wdCheck("eeo", "Do you have a disability? (Yes / No)", dc.dis, "No");
  wdCheck("eeo", "Gender identity (Claude picks from the list)", dc.gid, "Cisgender man");
  const vetMark = await t.js("const b = document.querySelector('[data-automation-id=\"formField-veteranStatusYN\"] button'); return !!b && b.hasAttribute('data-jh-filled')");
  wdCheck("eeo", "the corrected veteran answer is Autofill's (marked)", vetMark ? "marked" : "", "marked");
  await go(`${WD}?fx=flow&step=experience`, "document.readyState === 'complete' && window.__wd && window.__wd.loaded");
  await autofill();
  await t.until("document.querySelectorAll('[data-sec=work] > [role=group]').length === 2", 60000);
  const wd = await t.js("return [...document.querySelectorAll('[data-sec=work] > [role=group]')].map((g) => [g.querySelector('input[id$=jobTitle]')?.value, g.querySelector('input[id$=companyName]')?.value, g.querySelector('[id$=roleDescription]')?.value])");
  wdCheck("repeater", "Work Experience", JSON.stringify(wd.map((r) => r.slice(0, 2))), JSON.stringify(WANT_JOBS.map((j) => j.slice(0, 2))));
  wdCheck("textarea", "Role Description", wd[0] && wd[0][2], WANT_JOBS[0][7]);
  // 1.3.1 #2: the tailored resume's skills through Workday's prompt (async search, the parsed picks already there)
  await t.until("[...document.querySelectorAll('[data-automation-id=formField-skills] [data-automation-id=selectedItem]')].length >= 3", 30000);
  const skills = await t.js("return [...document.querySelectorAll('[data-automation-id=formField-skills] [data-automation-id=selectedItem]')].map((x) => x.title)");
  wdCheck("skills", "Skills", skills.join("|"), "Amazon Web Services (AWS)|Python|PyTorch");
  wdCheck("file", "Resume", await t.js("return !!document.querySelector('[data-automation-id=file-upload-successful], [data-automation-id=file-upload-item]')") ? "uploaded" : "", "uploaded");
  rows.push({ site: "Workday", board: "workday", cells: wdCells, clicks: await t.js("return (window.__wd.clicks || []).filter((c) => !c.trusted)"), marks: {} });
  }

  // ---- 1.3.1 #6: Sign In / Create Account with the job-site account from the extension's settings ----
  if (!only || only.test("accounts")) {
    const PW = "Zz-Test-Pass-1", EMAIL = "zz.testcase@example.com";
    const gen = PAGES.find((p) => p.board === "generic").host;
    await go(`https://${gen}/account/register`);
    await t.panelClick("Autofill");
    const reg = await t.until(`document.getElementById("reg-pw2").value === ${JSON.stringify(PW)}`, 20000) && await panelHas("Click Create Account", 15000);
    const regVals = await t.js(`return [document.getElementById("reg-email").value, document.getElementById("reg-pw").value === ${JSON.stringify(PW)},
      document.getElementById("reg-terms").checked, document.getElementById("hp-box").checked, window.__clicks.length]`);
    check("any site's Create Account: email, password, confirm password and the terms box; robots box left alone",
      reg && JSON.stringify(regVals) === JSON.stringify([EMAIL, true, true, false, 0]), regVals);
    check("Create Account: the panel says to click it", await panelHas("Click Create Account", 8000), (await t.panelText()).slice(0, 400));
    await go(`https://${gen}/account/signin`);
    await t.panelClick("Autofill");
    const si = await t.until(`document.getElementById("si-pw").value === ${JSON.stringify(PW)}`, 20000);
    check("any site's Sign In: user name and password, Sign In never clicked",
      si && (await t.js("return document.getElementById('si-user').value")) === EMAIL && (await t.js("return window.__clicks.length")) === 0,
      await t.js("return [document.getElementById('si-user').value, window.__clicks]"));
    const WDA = `${WD_JOB}/apply/applyManually`;
    await go(`${WDA}?fx=auth_create`);
    await t.panelClick("Autofill");
    const wdc = await t.until(`document.querySelector("[data-automation-id=verifyPassword]")?.value === ${JSON.stringify(PW)}`, 20000);
    check("Workday Create Account: email, password, verify password, terms box; never clicked",
      wdc && (await t.js(`return document.querySelector("input[data-automation-id=email]").value === ${JSON.stringify(EMAIL)} && !!document.querySelector("input[data-automation-id=createAccountCheckbox]")?.checked`)) &&
      (await t.js("return (window.__wd.clicks || []).filter((c) => !c.trusted).length")) === 0, await t.js("return window.__wd.clicks"));
    await go(`${WDA}?fx=auth_signin`);
    await t.panelClick("Autofill");
    check("Workday Sign In: email and password; never clicked", await t.until(`document.querySelector("[data-automation-id=password]")?.value === ${JSON.stringify(PW)}`, 20000) &&
      (await t.js("return (window.__wd.clicks || []).filter((c) => !c.trusted).length")) === 0);
    const leaked = await t.js(`return document.documentElement.outerHTML.includes(${JSON.stringify(PW)})`);
    check("the password is never written into the page's HTML", !leaked);
  }

  // ---- the panel's tools: Cover letter (paste / upload as PDF), Match %, Save job ----
  if (!only || only.test("tools")) {
    const byName = (n) => PAGES.find((p) => p.name === n);
    const br = byName("Breezy"), gh = byName("Greenhouse");
    await go(`https://${br.host}${br.path}`, "document.readyState === 'complete' && window.__sim && window.__sim.ready");
    await panelHas("Cover letter", 15000);
    await t.panelClick("Cover letter");
    const ready = await panelHas("Paste into the form", 20000);
    await t.panelClick("Paste into the form");
    const pasted = await t.until("document.getElementById('bz-cover').value.startsWith('Dear Hiring Team')", 8000);
    check("Cover letter: written on request and pasted into the cover letter box", ready && pasted, await t.js("return document.getElementById('bz-cover').value.slice(0, 80)"));
    check("Cover letter: the pasted letter is marked for review", await t.until("[...document.querySelectorAll('[data-jh-mark=review]')].length > 0", 4000));
    await t.panelClick("Match");
    check("Match: the job's match % and missing keywords in the panel", await panelHas("% with ", 10000) || await panelHas("Match: ", 1000), (await t.panelText()).slice(0, 600));
    await go(`https://${gh.host}${gh.path}`, "document.readyState === 'complete' && window.__sim && window.__sim.ready");
    await panelHas("Cover letter", 15000);
    await t.panelClick("Cover letter");
    await panelHas("Upload as PDF", 20000);
    await t.panelClick("Upload as PDF");
    const up = await t.until("document.getElementById('cover_letter').files.length === 1", 8000);
    const name = await t.js("return document.getElementById('cover_letter').files[0]?.name || ''");
    check("Cover letter: uploaded as a PDF named like the resume", up && /_Cover_Letter\.pdf$/.test(name), name);
  }

  for (const r of rows) {
    check(`${r.site}: never clicked Submit / Next`, !r.clicks.some((c) => !c.trusted), r.clicks);
    for (const [type, c] of Object.entries(r.cells)) check(`${r.site}: ${type}`, c.fail === 0, c.misses.join(" | "));
  }
  check("no page errors", t.errors.length === 0, t.errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
} finally {
  if (process.env.JH_DEBUG && t) {
    const { writeFileSync } = await import("node:fs");
    writeFileSync(process.env.JH_DEBUG, JSON.stringify({ logs: t.logs, rows }, null, 1));
  }
  b.close();
  console.log(JSON.stringify({ checks, rows }));
}

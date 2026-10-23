// GOAL30, the real Autofill extension in headless Chrome against a real job-hunt-agent server, on Workday-shaped steps:
// §0 NVIDIA's My Experience (panels with no aria-label: past jobs' Location is the employer's country, never the user's home
// city; "I currently work here" only on the current job; the school's city), §0b NVIDIA's Voluntary Disclosures with
// the last dropdown's list still on screen while the next opens (ethnicity, gender, veteran all filled by meaning),
// §1 / §3 Exelixis' Application Questions (the start date is today + 2 weeks in its own boxes, Location is the resume's
// city, a question with no saved answer offers its choices as chips and one click fills and saves, Location switches
// to where the user lives for this job and sticks through Autofill again). §5: the panel and Autofill are timed.
// usage: node goal30_ext.mjs <base> <token> <chrome> <profile dir> <extension dir> <fixtures dir>
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { launch, serveFixtures, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXT_DIR, FIX] = process.argv.slice(2);
const SHOTS = process.env.JH_SHOTS || "";               // a folder: the panel's screenshots for PROGRESS30-ext
const HOST = "zztest.wd5.myworkdayjobs.com";
const JOB = `https://${HOST}/en-US/External/job/Foster-City-CA/AI-Engineer_JR0001`;
const APPLY = `${JOB}/apply/applyManually`;
const checks = [];
const timings = {};
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: typeof detail === "string" ? detail : JSON.stringify(detail) });
const api = async (path, body) => {
  const r = await fetch(`${BASE}${path}${path.includes("?") ? "&" : "?"}profile=zztest`, { method: body ? "POST" : "GET",
    headers: { "X-Jobhunt-Token": TOKEN, "X-Jobhunt": "1", "Content-Type": "application/json" }, body: body ? JSON.stringify({ profile: "zztest", ...body }) : undefined });
  return r.json();
};
// §0 / §0b on other boards: tests/fixtures/autofill/goal30/make_boards.py
const BOARDS = { greenhouse: { host: "job-boards.greenhouse.io", path: "/zztest/jobs/30001" }, lever: { host: "jobs.lever.co", path: "/zztest/30002/apply" },
  ashby: { host: "jobs.ashbyhq.com", path: "/zztest/30003/application" }, icims: { host: "careers-zztest.icims.com", path: "/jobs/30004/ml-engineer/candidate" } };
const pad = (n) => String(n).padStart(2, "0");
const inTwoWeeks = (() => { const d = new Date(); d.setDate(d.getDate() + 14); return [pad(d.getMonth() + 1), pad(d.getDate()), String(d.getFullYear())]; })();

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
    const board = Object.entries(BOARDS).find(([, x]) => x.host === u.hostname);
    if (board) return { file: join(FIX, "goal30", "boards", `${board[0]}.html`), type: "text/html" };
    if (u.pathname === "/__sim/workday_sim.js") return { file: join(FIX, "workday_sim.js"), type: "text/javascript" };
    if (u.pathname.startsWith("/__sim/steps/")) return { file: join(FIX, "workday", "steps", u.pathname.split("/").pop()), type: "text/html" };
    return { file: join(FIX, "workday", `${u.searchParams.get("fx") || "job"}.html`), type: "text/html" };
  });
  const go = async (url) => { await t.send("Page.navigate", { url }); await t.until("document.readyState === 'complete' && window.__wd && window.__wd.loaded", 10000); };
  const panelHas = (text, ms = 8000) => (async () => { for (let i = 0; i < ms / 100; i++) { if ((await t.panelText()).includes(text)) return true; await sleep(100); } return false; })();
  const val = (sel) => t.js(`return document.querySelector(${JSON.stringify(sel)})?.value ?? null`);
  const btnText = (field) => t.js(`return document.querySelector('[data-automation-id="formField-${field}"] button[aria-haspopup]')?.textContent`);
  const work = (n, k) => t.js(`return document.querySelectorAll('[data-sec=work] > [role=group]')[${n}]?.querySelector('input[id$="--${k}"]')?.value ?? null`);
  const current = (n) => t.js(`return document.querySelectorAll('[data-sec=work] > [role=group]')[${n}]?.querySelector('[data-automation-id=currentlyWorkHere]')?.checked ?? null`);
  const area = "[data-automation-id='formField-ex2'] textarea";
  const click = async (text, ms = 10000) => {             // the panel draws its buttons a moment after the page
    for (let i = 0; i < ms / 200; i++) { if (await t.panelClick(text)) return true; await sleep(200); }
    return false;
  };
  // a panel button by its title (a chip "No" and "Not this job?" both start with "No")
  const clickTitle = async (title) => {
    const { root } = await t.send("DOM.getDocument", { depth: -1, pierce: true });
    const find = (n) => {
      const a = n.attributes || [];
      for (let i = 0; i < a.length; i += 2) if (a[i] === "title" && a[i + 1] === title && n.nodeName === "BUTTON") return n;
      for (const k of [...(n.children || []), ...(n.shadowRoots || [])]) { const f = find(k); if (f) return f; }
      return null;
    };
    const node = find(root);
    return Boolean(node) && t.clickNode(node.backendNodeId);
  };
  // a box in the panel (closed shadow root) by its aria-label: set like a person typing, then "change"
  const panelSet = async (label, value) => {
    const { root } = await t.send("DOM.getDocument", { depth: -1, pierce: true });
    const find = (n) => {
      const a = n.attributes || [];
      for (let i = 0; i < a.length; i += 2) if (a[i] === "aria-label" && a[i + 1] === label) return n;
      for (const k of [...(n.children || []), ...(n.shadowRoots || [])]) { const f = find(k); if (f) return f; }
      return null;
    };
    const node = find(root);
    if (!node) return null;
    const { object } = await t.send("DOM.resolveNode", { backendNodeId: node.backendNodeId });
    const { result } = await t.send("Runtime.callFunctionOn", { objectId: object.objectId, returnByValue: true, arguments: [{ value }],
      functionDeclaration: "function (v) { if (v !== null) { this.value = v; this.dispatchEvent(new Event('input', { bubbles: true })); this.dispatchEvent(new Event('change', { bubbles: true })); } return this.value; }" });
    return result.value;
  };
  // §5: in the page itself, from the user's click inside the panel to `text` painted in it (no test-harness waits counted)
  const timeTo = async (text) => {
    const { root } = await t.send("DOM.getDocument", { depth: -1, pierce: true });
    const find = (n) => {
      if (n.attributes && n.attributes.includes("data-jh-panel")) return n;
      for (const k of [...(n.children || []), ...(n.shadowRoots || [])]) { const f = find(k); if (f) return f; }
      return null;
    };
    const host = find(root);
    const { object } = await t.send("DOM.resolveNode", { backendNodeId: host.shadowRoots[0].backendNodeId });
    await t.send("Runtime.callFunctionOn", { objectId: object.objectId, arguments: [{ value: text }], functionDeclaration: `function (text) {
      const sr = this; window.__jhT = { click: null, shown: null };
      sr.addEventListener("click", () => { if (window.__jhT.click === null) window.__jhT.click = performance.now(); }, true);
      let seen = false;                                 // shown = the frame after it is in the DOM (painted), not just written
      new MutationObserver(() => { if (window.__jhT.click !== null && !seen && sr.textContent.includes(text)) { seen = true;
        requestAnimationFrame(() => setTimeout(() => { window.__jhT.shown = performance.now(); }, 0)); } })
        .observe(sr, { subtree: true, childList: true, characterData: true }); }` });
    return async () => t.js("const x = window.__jhT; return x && x.click !== null && x.shown !== null ? Math.round(x.shown - x.click) : null");
  };
  const shot = async (name) => {
    if (!SHOTS) return;
    const { data } = await t.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(SHOTS, `${name}.png`), Buffer.from(data, "base64"));
  };
  const clickTitled = async (title, ms = 10000) => {      // the panel may redraw between finding and clicking
    for (let i = 0; i < ms / 200; i++) { if (await clickTitle(title)) return true; await sleep(200); }
    return false;
  };

  // ---- the job posting: the panel opens (§5) ----
  let t0 = Date.now();
  await go(`${JOB}?fx=job`);
  check("the panel shows the matched job", await panelHas("ZZ Test Corp · AI Engineer"), await t.panelText());
  timings.panel_open_cold_ms = Date.now() - t0;        // the first page after Chrome starts (the extension wakes up)
  const warm = [];
  for (let i = 0; i < 3; i++) {                        // the usual case: Chrome already running
    t0 = Date.now();
    await go(`${JOB}?fx=job&n=${i}`);
    await panelHas("ZZ Test Corp · AI Engineer");
    warm.push(Date.now() - t0);
  }
  timings.panel_open_ms = [...warm].sort((a, b) => a - b)[1];    // the middle of three

  // ---- §0: NVIDIA's My Experience ----
  await go(`${APPLY}?fx=flow&step=experience_nvidia`);
  await t.until("document.querySelector('[data-automation-id=select-files]')", 8000);
  check("the panel offers Autofill on My Experience", await click("Autofill"));
  const filled = await t.until(`window.__wd.parsed && document.querySelectorAll('[data-sec=work] > [role=group]').length === 2 &&
    document.querySelectorAll('[data-sec=work] > [role=group]')[1].querySelector('input[id$="--companyName"]')?.value === 'Testworks'`, 90000);
  const shown = await t.panelText();
  check("the panel (and its collapsed tab) never says null", !/\bnull/.test(shown), shown.slice(-300));
  check("My Experience: the two jobs are filled", filled, [await work(0, "companyName"), await work(1, "companyName")]);
  await t.until(`/United States/.test(document.querySelectorAll('[data-sec=work] > [role=group]')[0]?.querySelector('input[id$="--location"]')?.value || "")`, 15000);
  const loc1 = await work(0, "location");
  check("§0 the current job's Location is its country, not the user's home city", /^United States( of America)?$/.test(loc1 || ""), loc1);
  check("§0 the past job's Location is India", (await work(1, "location")) === "India", await work(1, "location"));
  check("§0 I currently work here: ticked on the current job only", (await current(0)) === true && (await current(1)) === false,
    [await current(0), await current(1)]);
  await t.until("document.querySelectorAll('[data-sec=education] > [role=group]').length === 1 && document.querySelector('[data-sec=education] input[id$=\"--location\"]')?.value", 30000);
  const school = await t.js("return document.querySelectorAll('[data-sec=education] > [role=group]')[0]?.querySelector('input[id$=\"--location\"]')?.value ?? null");
  check("§0 the school's Location is Springfield, IL", school === "Springfield, IL",
    await t.js("return [...document.querySelectorAll('[data-sec=education] > [role=group]')].map((p) => [...p.querySelectorAll('input, button[aria-haspopup]')].map((i) => (i.id || i.getAttribute('aria-label') || '') + '=' + (i.value || i.textContent)))"));
  check("§0 no job field is answered from the user's home address", !(await t.js("return [...document.querySelectorAll('[data-sec=work] input')].some((i) => /Austin/.test(i.value))")),
    await t.js("return [...document.querySelectorAll('[data-sec=work] input')].map((i) => i.value)"));

  // ---- §0b: NVIDIA's Voluntary Disclosures, lists slow to go away ----
  await go(`${APPLY}?fx=flow&step=disclosures_nvidia&stale=1`);
  await click("Autofill");
  await t.until("document.querySelector('[data-automation-id=agreementCheckbox]')?.checked && !/Select One/.test(document.querySelector('[data-automation-id=\"formField-veteranStatus\"] button')?.textContent)", 30000);
  const eth = await btnText("ethnicity");
  check("§0b ethnicity: Asian, by meaning", /^Asian \(Not Hispanic/.test(eth || ""), eth);
  check("§0b gender: Male", (await btnText("gender")) === "Male", await btnText("gender"));
  check("§0b veteran: I am not a veteran (saved: No)", (await btnText("veteranStatus")) === "I am not a veteran", await btnText("veteranStatus"));
  check("§0b the terms box is ticked", await t.js("return document.querySelector('[data-automation-id=agreementCheckbox]').checked"));

  // ---- §1 / §3: Exelixis' Application Questions ----
  await go(`${APPLY}?fx=flow&step=questions_exelixis`);
  t0 = Date.now();
  await click("Autofill");
  const done = await t.until(`document.querySelector('[data-automation-id="formField-ex4"] button')?.textContent === 'No' && document.querySelector(${JSON.stringify(area)})?.value`, 20000);
  timings.autofill_to_last_field_ms = Date.now() - t0;
  check("§1 Exelixis: the questions the user answered are filled", done, [await btnText("ex1"), await btnText("ex3"), await btnText("ex4")]);
  const dates = await t.js("return [...document.querySelectorAll('[data-automation-id=\"formField-desiredStartDate\"] input')].map((i) => i.value)");
  check("§1.5 the desired start date is today + 2 weeks in its own boxes", JSON.stringify(dates) === JSON.stringify(inTwoWeeks), [dates, inTwoWeeks]);
  check("§1 age 18 / authorized / sponsorship from the user's answers", (await btnText("ex1")) === "Yes" && (await btnText("ex3")) === "Yes" && (await btnText("ex4")) === "No",
    [await btnText("ex1"), await btnText("ex3"), await btnText("ex4")]);
  const where = await val(area);
  check("§3 Location (City, State, Country) is the resume's city", /Foster City/.test(where || "") && !/Austin/.test(where || ""), where);
  check("§1.4 employed and HCP are never guessed", /Select One/.test(await btnText("ex5")) && /Select One/.test(await btnText("ex6")), [await btnText("ex5"), await btnText("ex6")]);
  await panelHas("Needs you (", 10000);
  const text = await t.panelText();
  check("Workday's header menus are not form questions", !/Careers at ZZ Test Corp|Search Careers/.test(text), text);
  check("§1.3 a question with no saved answer offers its choices", /Are you currently employed\?[\s\S]*Yes No/.test(text), text);
  check("§3 Location says where it comes from, with both chips", text.includes("From your resume for this job: Foster City, CA") && text.includes("Where you live: Austin, TX"), text);

  await shot("ux30-chips-and-location");

  // one chip click fills the page and saves the answer
  check("§1.3 the chip is there to click", await clickTitled("Fill “No” and save it"));
  await t.until("document.querySelector('[data-automation-id=\"formField-ex5\"] button')?.textContent === 'No' || document.querySelector('[data-automation-id=\"formField-ex6\"] button')?.textContent === 'No'", 8000);
  const ex5 = await btnText("ex5");
  const ex6 = await btnText("ex6");
  check("§1.3 a chip click fills the page field", ex5 === "No" || ex6 === "No", [ex5, ex6]);
  await sleep(800);
  const saved = JSON.stringify(await api("/api/autofill/answers"));
  check("§1.3 ... and saves the answer", /"key":\s*"(employed|hcp)"[^}]*"answer":\s*"No"/.test(saved), saved.slice(0, 600));

  // Location: where the user lives, for this job only, and it sticks through Autofill again (after the chip's row moved)
  await panelHas("Yours (1)", 8000);
  check("§3 the Where you live chip is there to click", await clickTitled("Your own city, for this job only"));
  await t.until(`/Austin/.test(document.querySelector(${JSON.stringify(area)})?.value || '')`, 8000);
  check("§3 the Location chip switches the field", /Austin/.test((await val(area)) || ""), await val(area));
  await click("Autofill");
  await sleep(2500);
  check("§3 ... and it sticks through Autofill again", /Austin/.test((await val(area)) || ""), await val(area));
  check("§3 the panel now says where you live", await panelHas("✓ Where you live: Austin, TX"), await t.panelText());
  await shot("ux30-location-where-you-live");

  // ---- §4: ✎ on each kind of row, kept where the user chose (Location: this job only by default; others everywhere) ----
  const LOC = "Location (City, State, Country)";
  check("§4 ✎ on a text box (Location)", await clickTitled(`Edit ${LOC}`));
  check("§4 Location's ✎ saves for this job only by default", (await panelSet(`Save the answer to ${LOC} for`, null)) === "job");
  await panelSet(`New answer for ${LOC}`, "Austin, TX");
  await click("Fill it");
  check("§4 the edit fills the page", await t.until(`document.querySelector(${JSON.stringify(area)})?.value === 'Austin, TX'`, 8000), await val(area));
  await click("Autofill");
  await sleep(2500);
  check("§3 / §4 a Location edit sticks through Autofill again", (await val(area)) === "Austin, TX", await val(area));
  const HCP = "Are you a Health Care Provider(HCP)?";
  check("§4 ✎ on a dropdown", await clickTitled(`Edit ${HCP}`));
  check("§4 a dropdown's ✎ saves everywhere by default", (await panelSet(`Save the answer to ${HCP} for`, null)) === "everywhere");
  await panelSet(`New answer for ${HCP}`, "Yes");
  await click("Fill it");
  check("§4 the dropdown takes the edit", await t.until("document.querySelector('[data-automation-id=\"formField-ex6\"] button')?.textContent === 'Yes'", 8000), await btnText("ex6"));
  await sleep(800);
  check("§4 ... saved everywhere as the user's answer", /"key":\s*"hcp"[^}]*"answer":\s*"Yes"/.test(JSON.stringify(await api("/api/autofill/answers"))));
  const START = "What is your desired start date?";
  check("§4 ✎ on a date", await clickTitled(`Edit ${START}`));
  await panelSet(`New answer for ${START}`, "12/01/2026");
  await click("Fill it");
  check("§4 the date boxes take the edit", await t.until("[...document.querySelectorAll('[data-automation-id=\"formField-desiredStartDate\"] input')].map((i) => i.value).join('/') === '12/01/2026'", 8000),
    await t.js("return [...document.querySelectorAll('[data-automation-id=\"formField-desiredStartDate\"] input')].map((i) => i.value)"));

  // ---- §2: an open question the user's saved answer fills, written for another company ----
  await go(`${APPLY}?fx=flow&step=questions_open`);
  await click("Autofill");
  check("§2 the user's saved answer fills the open question", await t.until("/Globex/.test(document.querySelector('[data-automation-id=\"formField-op1\"] textarea')?.value || '')", 15000));
  check("§2 the panel says whose job it was written for", await panelHas("Written for Globex"), await t.panelText());
  check("§2 ... and flags that it names Globex", await panelHas("▲Names Globex") || await panelHas("▲ Names Globex"), await t.panelText());
  check("§2 Regenerate for this job is offered", await panelHas("Regenerate for this job"), await t.panelText());
  await shot("ux30-regenerate");
  const writing = await timeTo("Writing");
  if (process.env.JH_PROFILE) { await t.send("Profiler.enable"); await t.send("Profiler.setSamplingInterval", { interval: 100 }); await t.send("Profiler.start"); }
  t0 = Date.now();
  await click("Regenerate for this job");
  if (process.env.JH_PROFILE) writeFileSync(process.env.JH_PROFILE, JSON.stringify((await t.send("Profiler.stop")).profile));
  check("§5 Regenerate shows Writing at once", await panelHas("Writing", 5000), await t.panelText());
  timings.generate_to_writing_ms = await writing();
  check("§5 ... and the new answer arrives", await panelHas("Ready", 30000) || await panelHas("I build retrieval", 1000), await t.panelText());
  timings.generate_to_ready_ms = Date.now() - t0;

  // ---- §0 / §0b on Greenhouse, Lever, Ashby, iCIMS ----
  const v = (id) => t.js(`return document.getElementById(${JSON.stringify(id)})?.value ?? null`);
  const on = (id) => t.js(`return document.getElementById(${JSON.stringify(id)})?.checked ?? null`);
  const radio = (name) => t.js(`return document.querySelector('input[name=${JSON.stringify(name)}]:checked')?.value ?? ""`);
  const board = async (name, last) => {
    await t.send("Page.navigate", { url: `https://${BOARDS[name].host}${BOARDS[name].path}` });
    await t.until("document.readyState === 'complete'", 10000);
    let done = false;                                   // a busy machine: an auto-fill already running ignores a press
    for (let i = 0; i < 3 && !done; i++) {
      check(`${name}: Autofill runs`, await click("Autofill"));
      done = await t.until(last, 12000);
    }
    return done;
  };
  const EEO = { gender: "Male", race: "Asian", veteran: "I am not a protected veteran" };

  await board("greenhouse", "document.getElementById('veteran_status').value && document.getElementById('employment--company-1').value");
  check("§0 greenhouse: each job in its own entry", /^Examplecorp/.test(await v("employment--company-0")) && /^Testworks/.test(await v("employment--company-1")),
    [await v("employment--company-0"), await v("employment--company-1")]);
  check("§0 greenhouse: Current role only on the current job", (await on("employment--current-role-0")) === true && (await on("employment--current-role-1")) === false,
    [await on("employment--current-role-0"), await on("employment--current-role-1")]);
  check("§0 greenhouse: the school", /Lakeview State/.test(await v("education--school-0")), await v("education--school-0"));
  check("§0b greenhouse: EEO by meaning", (await v("gender")) === EEO.gender && (await v("race")) === EEO.race && (await v("hispanic_ethnicity")) === "No" &&
    (await v("veteran_status")) === EEO.veteran, [await v("gender"), await v("race"), await v("hispanic_ethnicity"), await v("veteran_status")]);
  check("§0b greenhouse: disability (never saved) is left for the user and the panel says so", (await v("disability_status")) === "" && (await panelHas("▲ Disability Status")),
    [await v("disability_status"), await t.panelText()]);

  await board("lever", "document.getElementById('eeo[veteran]').value");
  check("§0b lever: EEO by meaning", (await v("eeo[gender]")) === EEO.gender && (await v("eeo[race]")) === EEO.race && (await v("eeo[veteran]")) === EEO.veteran,
    [await v("eeo[gender]"), await v("eeo[race]"), await v("eeo[veteran]")]);
  check("lever: the user's Location at the top", Boolean(await v("location")), await v("location"));

  await board("ashby", "document.querySelector('input[name=veteranStatus]:checked')");
  check("§0b ashby: EEO radios by meaning", (await radio("gender")) === EEO.gender && (await radio("race")) === EEO.race && (await radio("veteranStatus")) === EEO.veteran,
    [await radio("gender"), await radio("race"), await radio("veteranStatus")]);

  await board("icims", "document.getElementById('EEO_Veteran').value && document.getElementById('WorkExperience_1_Country').value");
  check("§0 icims: each job's Country is its employer's", (await v("WorkExperience_0_Country")) === "United States" && (await v("WorkExperience_1_Country")) === "India",
    [await v("WorkExperience_0_Country"), await v("WorkExperience_1_Country")]);
  check("§0 icims: no job's City or State is the user's home", !/Austin/.test(`${await v("WorkExperience_0_City")} ${await v("WorkExperience_1_City")}`) &&
    (await v("WorkExperience_0_State")) !== "Kansas" && (await v("WorkExperience_1_State")) !== "Kansas",
    [await v("WorkExperience_0_City"), await v("WorkExperience_0_State"), await v("WorkExperience_1_City"), await v("WorkExperience_1_State")]);
  check("§0 icims: This is my current job only on the current one", (await on("WorkExperience_0_Current")) === true && (await on("WorkExperience_1_Current")) === false,
    [await on("WorkExperience_0_Current"), await on("WorkExperience_1_Current")]);
  check("§0 icims: the school's City, State, Country", (await v("Education_0_City")) === "Springfield" && (await v("Education_0_State")) === "Illinois" &&
    (await v("Education_0_Country")) === "United States", [await v("Education_0_City"), await v("Education_0_State"), await v("Education_0_Country")]);
  check("§0 icims: the user's own City at the top is still the user's", Boolean(await v("AddressCity")), await v("AddressCity"));
  check("§0b icims: EEO by meaning", (await v("EEO_Gender")) === EEO.gender && (await v("EEO_Race")) === EEO.race && (await v("EEO_Veteran")) === EEO.veteran,
    [await v("EEO_Gender"), await v("EEO_Race"), await v("EEO_Veteran")]);

  // §4: ✎ on a radio group (Ashby's survey), everywhere
  await board("ashby", "document.querySelector('input[name=veteranStatus]:checked')");
  check("§4 ✎ on a radio group", await clickTitled("Edit Gender"));
  await panelSet("New answer for Gender", "Decline To Self Identify");
  await click("Fill it");
  check("§4 the radio group takes the edit", await t.until("document.querySelector('input[name=gender]:checked')?.value === 'Decline To Self Identify'", 8000), await radio("gender"));
} catch (err) {
  check("runner", false, String((err && err.stack) || err));
} finally {
  if (t) { timings.page_errors = t.errors.slice(0, 20); timings.page_logs = t.logs.slice(0, 40); }
  await b.close();
}
console.log(JSON.stringify({ checks, timings }));

// the real extension in headless Chrome on real saved apply pages (Greenhouse, Lever: tests/fixtures/autofill/
// boards, captured 2026-10-14) and the Workday fixture flow, against a real job-hunt-agent server with the fake person.
// A few questions are added to each page the way a site asks them; the user answers some by hand (trusted typing / clicks)
// and one from the panel; leaving the page saves them; the next board fills them. Plus the docked panel's layout,
// a forced adapter error, job-hunt-agent asleep, the update notice, and color-blind simulation screenshots.
// usage: node goal25_extension.mjs <base> <token> <chrome> <profile dir> <extension dir> <fixtures dir> <shots dir>
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { SHOW, launch, serveFixtures, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXT_DIR, FIX, SHOTS] = process.argv.slice(2);
const NEWER = process.env.JH_NEWER || "";
const OWN = JSON.parse(readFileSync(join(EXT_DIR, "manifest.json"), "utf8")).version;
const GH = "https://job-boards.greenhouse.io/doordashusa/jobs/3623888";
const LV = "https://jobs.lever.co/shieldai/2394368a-67ff-441c-83c3-e998446327e7/apply";
const WD_HOST = "zztest.wd5.myworkdayjobs.com";
const WD = `https://${WD_HOST}/en-US/External/job/Foster-City-CA/AI-Engineer_JR0001/apply/applyManually`;
const PASSWORD = "Zz&Test\"Pass-9!";
const LONG = "I want to work at DoorDash because local logistics is a hard, real problem. ".repeat(5).trim();
const checks = [];
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: typeof detail === "string" ? detail : JSON.stringify(detail) });
const api = async (path, body) => {
  const r = await fetch(`${BASE}${path}${path.includes("?") ? "&" : "?"}profile=zztest`, { method: body ? "POST" : "GET",
    headers: { "X-Jobhunt-Token": TOKEN, "X-Jobhunt": "1", "Content-Type": "application/json" }, body: body ? JSON.stringify({ profile: "zztest", ...body }) : undefined });
  return r.json();
};
const QUESTIONS = {
  greenhouse: `<div class="field"><label for="q_notice">What is your notice period?</label><input id="q_notice" type="text"></div>
    <div class="field"><label for="q_team">Preferred team size</label><select id="q_team"><option value="">Please select</option><option>1-5</option><option>6-20</option><option>20+</option></select></div>
    <fieldset class="field"><legend>Will you now or in the future require sponsorship for employment visa status?</legend><label><input type="radio" name="q_sp" value="Yes">Yes</label><label><input type="radio" name="q_sp" value="No">No</label></fieldset>
    <div class="field"><label for="q_why">Why do you want to work at DoorDash?</label><textarea id="q_why"></textarea></div>
    <div class="field"><label for="q_ssn">Social Security Number</label><input id="q_ssn" type="text"></div>`,
  lever: `<div class="application-question"><label for="l_notice">Notice period</label><input id="l_notice" type="text"></div>
    <div class="application-question"><label for="l_team">What is your preferred team size?</label><select id="l_team"><option value="">Select…</option><option>1-5</option><option>6-20</option><option>20+</option></select></div>
    <fieldset class="application-question"><legend>Will you now, or in the future, require sponsorship for employment visa status (e.g. H-1B)?</legend><label><input type="radio" name="l_sp" value="Yes">Yes</label><label><input type="radio" name="l_sp" value="No">No</label></fieldset>
    <div class="application-question"><label for="l_why">Why Shield AI?</label><textarea id="l_why"></textarea></div>`,
  workday: `<div data-automation-id="formField-noticePeriod"><label for="wd_notice">What is your notice period?</label><input id="wd_notice" type="text"></div>`,
};

const b = await launch(CHROME, PROFILE_DIR);
let t;
try {
  const { id } = await b.send("Extensions.loadUnpacked", { path: EXT_DIR });
  const o = await tab(b, `chrome-extension://${id}/options.html`);
  await o.until("document.getElementById('base')");
  const settings = (base) => o.js(`const $ = (i) => document.getElementById(i);
    $("base").value = ${JSON.stringify(base)}; $("token").value = ${JSON.stringify(TOKEN)};
    document.querySelector("input[name=profile][value=zztest]") || document.querySelector(".seg").append(Object.assign(document.createElement("input"), {type: "radio", name: "profile", value: "zztest"}));
    document.querySelector("input[name=profile][value=zztest]").checked = true;
    $("email").value = "zz.testcase@example.com"; $("password").value = ${JSON.stringify(PASSWORD)};
    $("form").requestSubmit(); await new Promise((r) => setTimeout(r, 300)); return (await chrome.storage.local.get("base")).base`);
  await settings(BASE);

  t = await tab(b);
  // the extension's own world in the page (its content scripts): read the panel's layout, force an adapter error
  let extCtx = null, mainFrame = null;
  b.on((m) => {
    if (m.sessionId !== t.sessionId || m.method !== "Runtime.executionContextCreated") return;
    const c = m.params.context;
    if (c.name === "job-hunt-agent Autofill" && c.auxData && c.auxData.frameId === mainFrame && c.auxData.type === "isolated") extCtx = c.id;
  });
  mainFrame = (await t.send("Page.getFrameTree")).frameTree.frame.id;
  const ext = async (expr) => {
    for (let i = 0; i < 40 && !extCtx; i++) await sleep(100);
    const r = await t.send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, contextId: extCtx, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  await serveFixtures(t, b, ["job-boards.greenhouse.io", "jobs.lever.co", WD_HOST], (u) => {
    if (u.hostname === "job-boards.greenhouse.io") return { file: join(FIX, "boards", "greenhouse.html"), type: "text/html" };
    if (u.hostname === "jobs.lever.co") return { file: join(FIX, "boards", "lever.html"), type: "text/html" };
    if (u.pathname === "/__sim/workday_sim.js") return { file: join(FIX, "workday_sim.js"), type: "text/javascript" };
    if (u.pathname.startsWith("/__sim/steps/")) return { file: join(FIX, "workday", "steps", u.pathname.split("/").pop()), type: "text/html" };
    return { file: join(FIX, "workday", `${u.searchParams.get("fx") || "job"}.html`), type: "text/html" };
  });
  const go = async (url, ready = "document.readyState === 'complete'") => {
    extCtx = null;
    await t.send("Page.navigate", { url });
    await t.until(ready, 15000);
    await sleep(1200);
  };
  const panelHas = async (text, ms = 10000) => { for (let i = 0; i < ms / 200; i++) { if ((await t.panelText()).includes(text)) return true; await sleep(200); } return false; };
  const inject = (html) => t.js(`const form = document.querySelector("#application-form, .application-form, [data-automation-id^=applyFlow], form") || document.body;
    const box = document.createElement("div"); box.id = "jh-test-q"; box.innerHTML = ${JSON.stringify(html)}; form.prepend(box); box.scrollIntoView(); return true;`);
  const val = (sel) => t.js(`return document.querySelector(${JSON.stringify(sel)})?.value ?? null`);
  const checked = (name) => t.js(`return document.querySelector('input[name=${name}]:checked')?.value || ""`);
  const typeInto = async (sel, text) => { await t.clickSel(sel); await t.send("Input.insertText", { text }); await sleep(150); };
  const layout = () => ext("return JH.panel.layout()");
  const margin = () => t.js("return getComputedStyle(document.documentElement).marginRight");
  const filled = async (ms = 150000) => { await sleep(800); for (let i = 0; i < ms / 300; i++) { const s = await t.panelText(); if (s.includes("Filled ") && !s.includes("Filling…")) return true; await sleep(300); } return false; };
  // a panel control by one of its attributes (the panel is a closed shadow root: reached through the DOM domain)
  async function panelNodes() {
    const { root } = await t.send("DOM.getDocument", { depth: -1, pierce: true });
    const out = [];
    const walk = (n) => { out.push(n); for (const k of [...(n.children || []), ...(n.shadowRoots || [])]) walk(k); };
    const find = (n) => (n.attributes && n.attributes.includes("data-jh-panel") ? n : [...(n.children || []), ...(n.shadowRoots || [])].map(find).find(Boolean));
    const host = find(root);
    if (host) walk(host);
    return out;
  }
  const attr = (n, name) => { const a = n.attributes || []; const i = a.indexOf(name); return i >= 0 && i % 2 === 0 ? a[i + 1] : null; };
  const clickNode = (node) => t.clickNode(node.backendNodeId);
  async function panelBy(name, test) {
    for (let i = 0; i < 4; i++) {                       // the panel may redraw between finding and clicking
      const node = (await panelNodes()).find((n) => { const v = attr(n, name); return v != null && (typeof test === "string" ? v === test : test(v)); });
      if (!node) return null;
      if (await clickNode(node)) return node;
    }
    return null;
  }
  async function panelSelect(label, value) {
    const node = (await panelNodes()).find((n) => n.nodeName === "SELECT" && attr(n, "aria-label") === label);
    if (!node) return false;
    const { object } = await t.send("DOM.resolveNode", { backendNodeId: node.backendNodeId });
    await t.send("Runtime.callFunctionOn", { objectId: object.objectId, functionDeclaration: "function (v) { this.value = v; return this.value; }", arguments: [{ value }] });
    return true;
  }
  const overlaps = (a, r) => Boolean(a) && r.right > a.x && r.left < a.x + a.width && r.bottom > a.y && r.top < a.y + a.height;
  const navButtons = () => t.js(`return [...document.querySelectorAll("button, input[type=submit], a, [role=button]")].filter((b) => /^(submit( application)?|apply|next|save( and continue)?|continue|review)$/i.test((b.innerText || b.value || "").trim()))
    .map((b) => { const r = b.getBoundingClientRect(); return { text: (b.innerText || b.value).trim(), left: r.left, right: r.right, top: r.top, bottom: r.bottom, w: r.width }; }).filter((r) => r.w > 0)`);

  // ---- Greenhouse: the docked panel, Autofill, hand answers, an edit in the panel ---------------------------------
  await go(GH);
  check("Greenhouse: the panel docks with the job, resume, address, sponsorship slot and profile",
    await panelHas("DoorDash · Shift Lead") && await panelHas("Tailored resume") && await panelHas("808 Comet Dr, Foster City, CA 94404") &&
    await panelHas("Future sponsorship question: No") && await panelHas("Zztest") && (await panelNodes()).some((n) => attr(n, "data-slot") === "sponsorship"), await t.panelText());
  check("the panel shows the extension version", await panelHas(`v${OWN}`), await t.panelText());
  check("a newer extension in job-hunt-agent shows the update notice", await panelHas(`Update ${NEWER} is ready: click ↻`), await t.panelText());
  const docked = await layout();
  const vw = await t.js("return innerWidth");
  check("docked on the right, 340 px, the page moved over", docked.mode === "dock" && !docked.collapsed && Math.round(docked.rect.width) === 340 &&
    Math.round(docked.rect.x + docked.rect.width) === vw && (await margin()) === "340px", [docked, vw, await margin()]);
  await inject(QUESTIONS.greenhouse);
  await t.panelClick("Autofill");
  const ghDone = await filled();
  const firstName = await t.js(`const l = [...document.querySelectorAll("label")].find((x) => /^First Name/.test(x.textContent.trim())); return document.getElementById(l?.htmlFor)?.value`);
  check("Greenhouse: Autofill fills what it knows", ghDone && firstName === "Zz" && (await checked("q_sp")) === "No", [firstName, await checked("q_sp"), await t.panelText()]);
  const gh = await layout();
  const buttons = await navButtons();
  const covered = buttons.filter((r) => overlaps(gh.rect, r));
  check("the page's own buttons are not under the panel", covered.length === 0 && buttons.some((r) => /submit|apply/i.test(r.text)), [covered, buttons]);
  check("Greenhouse: the field list is grouped with icons and words", await panelHas("▲ Needs you (") && await panelHas("✓ Filled ("), await t.panelText());
  await typeInto("#q_notice", "2 weeks");
  await t.clickSel("input[name=q_sp][value=Yes]");
  await typeInto("#q_why", LONG);
  await typeInto("#q_ssn", "123-45-6789");
  check("a hand-typed answer shows under Yours", await panelHas("✎ Yours (", 6000) && await panelHas("2 weeks"), await t.panelText());
  await panelBy("title", "Edit Preferred team size");
  await sleep(300);
  await panelSelect("New answer for Preferred team size", "6-20");
  await t.panelClick("Fill it");
  check("an edit in the panel fills the page field", await t.until("document.getElementById('q_team').value === '6-20'", 6000), await val("#q_team"));
  await t.js("window.scrollTo(0, document.body.scrollHeight); document.activeElement?.blur(); return true");
  await t.panelClick("What is your notice period?");
  check("clicking a row scrolls to and highlights the field", await t.until("document.activeElement?.id === 'q_notice' && !!document.querySelector('[data-jh-flash]')", 3000),
    await t.js("return document.activeElement?.id"));
  await sleep(1500);                                   // the stage reaches the service worker

  // ---- Lever: what the user answered on Greenhouse fills here ----------------------------------------------------------
  await go(LV);
  check("leaving the page says Saved for next time", await panelHas("Saved for next time") && await panelHas("Notice period = 2 weeks"), await t.panelText());
  check("the company-only answer says so", await panelHas("Saved for DoorDash only"), await t.panelText());
  check("the long answer is kept with the application", await panelHas("Saved with this application"), await t.panelText());
  const page = await api("/api/autofill/answers");
  check("the SSN typed by hand is not learned", !JSON.stringify(page).includes("123-45-6789"), page.learned);
  await inject(QUESTIONS.lever);
  await t.panelClick("Autofill");
  await filled();
  check("Lever: the notice period learned on Greenhouse fills", (await val("#l_notice")) === "2 weeks", await val("#l_notice"));
  check("Lever: the team size picked in the panel fills", (await val("#l_team")) === "6-20", await val("#l_team"));
  check("Lever: sponsorship stays the usual answer at another company", (await checked("l_sp")) === "No", await checked("l_sp"));
  check("Lever: the long answer is offered, not filled", (await val("#l_why")) === "", await val("#l_why"));
  { const n = (await panelNodes()).find((x) => attr(x, "title") === "Same as Mark applied in job-hunt-agent");   // the list's very end
    const { object } = await t.send("DOM.resolveNode", { backendNodeId: n.backendNodeId });
    const { result } = await t.send("Runtime.callFunctionOn", { objectId: object.objectId, returnByValue: true, functionDeclaration: `function () { ${SHOW} const r = this.getBoundingClientRect(); return [r.top, r.bottom, innerHeight, r.height]; }` });
    const [top, bottom, height, tall] = result.value;
    check("a long field list scrolls inside the panel (the last rows can be reached)", tall > 0 && top >= 0 && bottom <= height, result.value); }
  check("Lever: the long question waits under Needs you", await panelHas("▲ Needs you (") && Boolean(await panelBy("title", "Edit Why Shield AI?")), await t.panelText());
  await sleep(300);
  await t.panelClick("Use a saved answer ▾");
  await sleep(1200);
  await panelBy("title", "Why do you want to work at DoorDash?");
  await sleep(200);
  await t.panelClick("Fill it");
  check("Lever: Use a saved answer fills the box", await t.until(`document.getElementById('l_why').value === ${JSON.stringify(LONG)}`, 6000), (await val("#l_why"))?.slice(0, 60));
  await sleep(1500);

  // ---- Workday: the same learned answer on a Workday step; the fixed Next bar stays clear --------------------------
  await go(`${WD}?fx=flow&step=questions1`, "document.readyState === 'complete' && window.__wd && window.__wd.loaded");
  await inject(QUESTIONS.workday);
  let wdFilled = false;                                // a busy machine: an auto-fill already running ignores the first press
  for (let i = 0; i < 3 && !wdFilled; i++) {
    await t.panelClick("Autofill");
    wdFilled = await t.until("document.getElementById('wd_notice')?.value === '2 weeks'", 30000);
  }
  check("Workday: the learned answer fills a Workday question", wdFilled, [await val("#wd_notice"), await t.panelText()]);
  const wd = await layout();
  const next = await t.js(`const r = document.querySelector("[data-automation-id=bottom-navigation-next-button]").getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }`);
  check("Workday's fixed Next bar stays clear", !overlaps(wd.rect, next) && !wd.collapsed, [wd, next]);
  const undoable = await panelHas("Saved with this application");
  await t.panelClick("Undo");
  await sleep(800);
  const snips = await api("/api/autofill/snippets?q=Why");
  check("Undo in the panel removes what was learned", undoable && snips.snippets.length === 1 && snips.snippets[0].company === "DoorDash" && await panelHas("Undone"),
    [snips.snippets.map((s) => s.company), await t.panelText()]);

  // ---- a closed tab loses nothing ---------------------------------------------------------------------------------
  const t2 = await tab(b);
  await serveFixtures(t2, b, ["job-boards.greenhouse.io"], () => ({ file: join(FIX, "boards", "greenhouse.html"), type: "text/html" }));
  await t2.send("Page.navigate", { url: GH });
  await t2.until("document.readyState === 'complete'", 15000);
  await sleep(2500);
  await t2.js(`const form = document.querySelector("#application-form, form"); const box = document.createElement("div"); box.innerHTML = ${JSON.stringify(QUESTIONS.greenhouse)}; form.prepend(box); return true`);
  await t2.clickSel("#q_notice");
  await t2.send("Input.insertText", { text: "3 weeks" });
  await sleep(1800);                                   // staged with the service worker
  const targets = (await b.send("Target.getTargets")).targetInfos.filter((x) => x.type === "page" && x.url.startsWith(GH));
  await b.send("Target.closeTarget", { targetId: targets[targets.length - 1].targetId });
  let saved = "";
  for (let i = 0; i < 25 && saved !== "3 weeks"; i++) { await sleep(300); saved = (await api("/api/autofill/answers")).bank.find((r) => r.key === "notice")?.answer; }
  check("a closed tab loses nothing", saved === "3 weeks", saved);

  // ---- a form that is not a job application teaches it nothing ------------------------------------------------------
  const t3 = await tab(b);
  await serveFixtures(t3, b, ["shop.zztest.example"], () => ({ file: join(FIX, "generic_skills.html"), type: "text/html" }));
  await t3.send("Page.navigate", { url: "https://shop.zztest.example/newsletter" });
  await t3.until("document.readyState === 'complete'", 15000);
  await sleep(2000);
  await t3.clickSel("#fn");
  await t3.send("Input.insertText", { text: "ZZ Newsletter Name" });
  await sleep(1800);
  await t3.send("Page.navigate", { url: "about:blank" });   // leaving the page: a job page's answers would go now
  await sleep(2500);
  const notLearned = (await api("/api/autofill/answers")).bank.every((r) => r.answer !== "ZZ Newsletter Name");
  check("a form that is not a job application is not learned", notLearned, (await api("/api/autofill/answers")).bank.filter((r) => /Newsletter/.test(r.answer || "")));
  await b.send("Target.closeTarget", { targetId: t3.targetId });

  // ---- production guards: a forced adapter error, job-hunt-agent asleep ----------------------------------------------------
  await ext("await chrome.storage.local.set({ auto: false }); return true");   // only the forced run below fills this page
  await go(GH);
  await inject(QUESTIONS.greenhouse);
  await t.js(`const d = document.createElement("div"); d.className = "field";                 // a question whose id does not say it
    d.innerHTML = '<label for="question_77">Date of birth</label><input id="question_77" type="text" value="ZZ-DOB-1990">';
    document.getElementById("jh-test-q").append(d); return true`);
  await ext(`JH.boards.find((x) => x.id === "greenhouse").fill = async () => { throw new Error("forced adapter error (test)"); }; return true`);
  await t.panelClick("Autofill");
  await panelHas("tripped Autofill", 150000);
  check("a forced adapter error does not break the page", (await val("#q_notice")) === "3 weeks" && await panelHas("tripped Autofill"), [await val("#q_notice"), await t.panelText()]);
  const twice = await ext(`let runs = 0; const el = document.getElementById("q_notice");
    const first = JH.fill.once(el, async () => { runs++; await JH.sleep(300); return "first"; }, "busy");
    const second = await JH.fill.once(el, async () => { runs++; return "second"; }, "busy");
    return { runs, second, first: await first }`);
  check("one filler per field: a second filler waits its turn out", twice.runs === 1 && twice.second === "busy" && twice.first === "first", twice);
  let health = { errors: [] };
  for (let i = 0; i < 20 && !health.errors.length; i++) { await sleep(300); health = await api("/api/autofill/health/report"); }
  check("the error is reported to job-hunt-agent with the page", health.errors.some((e) => e.board === "greenhouse" && e.kind === "error" && /forced adapter error/.test(e.message)), health.errors);
  await settings("http://127.0.0.1:9");
  await t.panelClick("Autofill");
  check("job-hunt-agent asleep: the panel says so before filling", await panelHas("job-hunt-agent is not answering") && await panelHas("asleep or offline"), await t.panelText());
  await settings(BASE);
  await ext("await chrome.storage.local.set({ auto: true }); return true");

  // ---- color-blind check: every status is an icon + a word; screenshots in deuteranopia / protanopia ---------------
  await t.panelClick("Autofill");
  await filled();
  const text = await t.panelText();
  check("every status has an icon and a word", ["▲ Needs you (", "✓ Filled ("].every((s) => text.includes(s)) && /– Skipped \(|✎ Yours \(/.test(text), text);
  await t.js("window.scrollTo(0, 0); return true");
  const shots = [];
  for (const [name, type] of [["normal", "none"], ["deuteranopia", "deuteranopia"], ["protanopia", "protanopia"]]) {
    await t.send("Emulation.setEmulatedVisionDeficiency", { type });
    await sleep(400);
    const { data } = await t.send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(SHOTS, `panel-${name}.png`), Buffer.from(data, "base64"));
    shots.push(name);
  }
  await t.send("Emulation.setEmulatedVisionDeficiency", { type: "none" });
  check("simulation screenshots saved", shots.length === 3, shots);

  // ---- collapse to a tab, float (remembered per site), a narrow window ---------------------------------------------
  await t.panelClick("›");
  await sleep(300);
  const tabbed = await layout();
  const opener = (v) => v.startsWith("Open the Autofill panel");
  check("collapse gives a slim tab and gives the page its width back", tabbed.collapsed && !tabbed.rect && (await margin()) === "0px" &&
    Boolean((await panelNodes()).find((n) => opener(attr(n, "aria-label") || ""))), [tabbed, await margin()]);
  await panelBy("aria-label", opener);
  await sleep(300);
  const back = await layout();
  check("the tab opens it again", !back.collapsed && Boolean(back.rect) && (await margin()) === "340px", [back, await margin()]);
  await t.panelClick("⇱");
  await sleep(300);
  const floated = await layout();
  check("float mode is a draggable box", floated.mode === "float" && Boolean(floated.rect) && Math.round(floated.rect.width) === 320 && (await margin()) === "0px", [floated, await margin()]);
  await go(GH);
  await panelHas("DoorDash");
  check("float is remembered for this site", (await layout()).mode === "float", await layout());
  await t.panelClick("⇥");
  await sleep(300);
  await t.send("Emulation.setDeviceMetricsOverride", { width: 900, height: 900, deviceScaleFactor: 1, mobile: false });
  await go(GH);
  await panelHas("DoorDash");
  const narrow = await layout();
  check("a narrow window opens it collapsed", narrow.collapsed && (await margin()) === "0px", [narrow, await margin()]);
  await t.send("Emulation.clearDeviceMetricsOverride");
  check("no page errors", t.errors.length === 0, t.errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
} finally {
  if (process.env.JH_DEBUG && t) writeFileSync(process.env.JH_DEBUG, JSON.stringify({ logs: t.logs, panel: await t.panelText().catch(() => "") }, null, 1));
  b.close();
  console.log(JSON.stringify({ checks }));
}

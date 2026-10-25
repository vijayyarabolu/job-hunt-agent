// GOAL36 §5.2 to §5.4: the pages the extension could not read (tests/fixtures/autofill/goal36/sites), each served at an
// address of its own: a form in a frame from an unknown address, a form drawn 6 s after load, a form in a shadow root,
// a form written into an about:blank frame, a two-step form, a company site known only by company + title (JSON-LD),
// a posting with no form. Per page: does the panel show, how many fields does it list, which job. In "after" mode also
// the checks: Generate says why it cannot write on an unlinked page (Retry, Link to a job), writes on a matched one,
// and offers "Generate for this job" next to a saved answer without touching it; Scan again on the no-form page.
// Never clicks Next or Submit (the step test's Save and Continue is the person's click, logged as trusted).
// usage: node goal36_scan.mjs <base> <token> <chrome> <profile dir> <extension dir> <fixtures dir> <before|after>
import { join } from "node:path";
import { launch, serveFixtures, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXT_DIR, FIX, MODE = "after", ATS_JSON = "[]"] = process.argv.slice(2);
// GOAL27's apply pages of the known ATSs (Greenhouse, Lever, Ashby, iCIMS, SmartRecruiters, Taleo, SuccessFactors ...)
// and the Workday simulator's info step: fields found on load, before / after (no Autofill click)
const ATS_PAGES = JSON.parse(ATS_JSON);
const WD_HOST = "zztest.wd5.myworkdayjobs.com";
const WD_INFO = `https://${WD_HOST}/en-US/External/job/Springfield-MO/ML-Engineer_JR0027/apply/applyManually?fx=flow&step=info`;
const SITES = join(FIX, "goal36", "sites");
const AFTER = MODE === "after";
const PAGES = [
  { name: "iframe", url: "https://careers.zztest-iframe.com/jobs/ml-engineer", file: "iframe_top.html", wait: 8000 },
  { name: "late", url: "https://apply.zztest-late.com/careers/apply/36002", file: "late.html", wait: 13000 },
  { name: "shadow", url: "https://careers.zztest-shadow.com/apply/36003", file: "shadow.html", wait: 8000 },
  { name: "blank frame", url: "https://careers.zztest-blank.com/apply/36004", file: "blank_frame.html", wait: 8000 },
  { name: "steps", url: "https://apply.zztest-steps.com/careers/apply/36005", file: "steps.html", wait: 7000 },
  { name: "unknown job", url: "https://www.zztest-robotics.com/careers/ml-engineer", file: "unknown_job.html", wait: 8000 },
  { name: "no form", url: "https://careers.zztest-noform.com/jobs/36006", file: "no_form.html", wait: 4000 },
];
const FRAME_HOST = "forms.zztest-embed.net";
const JOB = "ZZ_TEST Prairie Robotics · Machine Learning Engineer";
const FIELDS = 7;                                    // first, last, email, phone, resume and two written questions
const checks = [], measure = [];
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: typeof detail === "string" ? detail.slice(0, 1500) : JSON.stringify(detail).slice(0, 1500) });

const b = await launch(CHROME, PROFILE_DIR);
try {
  const { id } = await b.send("Extensions.loadUnpacked", { path: EXT_DIR });
  const o = await tab(b, `chrome-extension://${id}/options.html`);
  await o.until("document.getElementById('base')");
  await o.js(`const $ = (i) => document.getElementById(i);
    $("base").value = ${JSON.stringify(BASE)}; $("token").value = ${JSON.stringify(TOKEN)};
    document.querySelector("input[name=profile][value=zztest]") || document.querySelector(".seg").append(Object.assign(document.createElement("input"), {type: "radio", name: "profile", value: "zztest"}));
    document.querySelector("input[name=profile][value=zztest]").checked = true; $("test").click()`);
  check("the extension reaches job-hunt-agent", await o.until("document.getElementById('test-out').textContent.startsWith('Connected as zztest')"));

  const t = await tab(b);
  const mainFrame = (await t.send("Page.getFrameTree")).frameTree.frame.id;
  let extCtx = null;
  b.on((m) => {
    if (m.sessionId !== t.sessionId || m.method !== "Runtime.executionContextCreated") return;
    const c = m.params.context;
    if (c.name === "job-hunt-agent Autofill" && c.auxData && c.auxData.frameId === mainFrame && c.auxData.type === "isolated") extCtx = c.id;
  });
  const ext = async (expr) => (await t.send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, contextId: extCtx, awaitPromise: true, returnByValue: true })).result?.value;
  const hosts = [...PAGES.map((p) => new URL(p.url).hostname), FRAME_HOST, ...ATS_PAGES.map((p) => p.host), WD_HOST];
  const SITES27 = join(FIX, "goal27", "sites");
  await serveFixtures(t, b, hosts, (u) => {
    if (u.hostname === FRAME_HOST) return { file: join(SITES, "iframe_inner.html"), type: "text/html" };
    if (u.pathname === "/__sim/ats_sim.js") return { file: join(FIX, "goal27", "ats_sim.js"), type: "text/javascript" };
    if (u.pathname === "/__sim/workday_sim.js") return { file: join(FIX, "workday_sim.js"), type: "text/javascript" };
    if (u.pathname.startsWith("/__sim/steps/")) return { file: join(FIX, "workday", "steps", u.pathname.split("/").pop()), type: "text/html" };
    if (u.hostname === WD_HOST) return { file: join(FIX, "workday", `${u.searchParams.get("fx") || "job"}.html`), type: "text/html" };
    const ats = ATS_PAGES.find((p) => p.host === u.hostname && (u.pathname === p.path || u.pathname + u.search === p.path))
      || ATS_PAGES.find((p) => p.host === u.hostname && u.pathname.startsWith(p.path) && p.file.endsWith("_form.html"));
    if (ats) return { file: join(SITES27, ats.file), type: "text/html" };
    const page = PAGES.find((p) => new URL(p.url).hostname === u.hostname && new URL(p.url).pathname === u.pathname);
    return page ? { file: join(SITES, page.file), type: "text/html" } : null;
  });
  const panelHas = async (text, ms = 10000) => { for (let i = 0; i < ms / 200; i++) { if ((await t.panelText()).includes(text)) return true; await sleep(200); } return false; };
  const state = () => ext("const s = JH.panel && JH.panel.get(); return s ? { rows: (s.rows || []).map((r) => r.label), job: s.job ? `${s.job.company} · ${s.job.title}` : null } : null");
  const shown = () => t.js("return !!document.querySelector('[data-jh-panel]')");
  async function look(name, extra = {}) {
    const s = (await state()) || { rows: [], job: null };
    const row = { page: name, shown: await shown(), rows: s.rows.length, job: s.job, ...extra };
    measure.push(row);
    return { ...row, labels: s.rows };
  }

  const only = process.env.JH_ONLY ? new RegExp(process.env.JH_ONLY, "i") : null;     // a debugging run: some pages only
  for (const p of PAGES.filter((x) => !only || only.test(x.name))) {
    await t.send("Page.navigate", { url: p.url });
    await t.until("document.readyState === 'complete'", 10000);
    await sleep(p.wait);
    if (p.name === "no form") {                       // the toolbar button opens the panel on any page
      const opened = await ext("if (!JH.panel.shown()) JH.top.message({ type: 'togglePanel' }); return JH.panel.shown()");
      await sleep(5000);
      const text = await t.panelText();
      await look(p.name, { says_why: /click Apply yourself/.test(text), opened });
      if (AFTER) {
        check("no form: the panel says why in words", text.includes("This page shows the job, not the form: click Apply yourself, then Scan again"), text);
        check("no form: Scan again is offered", text.includes("Scan again"), text);
        await t.panelClick("Scan again");
        await sleep(4500);
        check("no form: Scan again looks again and says why again", (await t.panelText()).includes("click Apply yourself"), await t.panelText());
      }
      continue;
    }
    if (p.name === "steps") {
      const one = await look("steps (step 1)");
      await t.clickSel("#next");                      // the user's click on Save and Continue
      await t.until("window.__step === 2", 8000);
      await sleep(5000);
      const two = await look("steps (step 2)");
      if (AFTER) {
        check("steps: step 1 is listed", one.shown && one.labels.includes("Email"), one);
        check("steps: step 2 is scanned on its own, with nothing typed", two.labels.includes("Describe a time you shipped a model to production"), two);
        check("steps: the only click on Save and Continue is the person's", JSON.stringify(await t.js("return window.__clicks")) === "[true]", await t.js("return window.__clicks"));
      }
      continue;
    }
    const got = await look(p.name);
    if (!AFTER) continue;
    check(`${p.name}: the panel shows on its own`, got.shown, got);
    check(`${p.name}: every field is listed`, got.rows >= FIELDS, got.labels);
    if (p.name === "iframe") {                        // no job for this page: Generate says why, never nothing
      check("iframe: the panel says the page is not linked to a job", (await t.panelText()).includes("This page is not linked to a job yet"), await t.panelText());
      await t.panelClick("Generate");
      check("iframe: Generate on an unlinked page says why", await panelHas("this page is not linked to a job yet, so Claude has no JD to write from"), await t.panelText());
      const text = await t.panelText();
      check("iframe: with an icon, Retry and Link to a job", /✕\s*No answer: this page is not linked/.test(text) && text.includes("Retry") && text.includes("Link to a job"), text);
    }
    if (p.name === "unknown job") {
      check("unknown job: matched by the company and title on the page", got.job === JOB, got);
      await t.panelClick("Generate");
      check("unknown job: Generate writes the answer for this job", await panelHas("✓ Ready", 15000), await t.panelText());
      await t.panelClick("Autofill");
      check("unknown job: Autofill fills the saved answer", await t.until("document.getElementById('u-proud').value.startsWith('ZZ_TEST saved')", 20000),
        await t.js("return document.getElementById('u-proud').value"));
      await sleep(2500);
      const text = await t.panelText();
      check("unknown job: Generate for this job is offered next to the saved answer", text.includes("Your saved answer is filled") && text.includes("Generate for this job"), text);
      await t.panelClick("Generate for this job");
      await sleep(4000);
      const ready = ((await t.panelText()).match(/✓ Ready/g) || []).length;
      check("unknown job: Generate for this job writes a new one", ready >= 2, await t.panelText());
      check("unknown job: the saved answer stays in the box", (await t.js("return document.getElementById('u-proud').value")).startsWith("ZZ_TEST saved"),
        await t.js("return document.getElementById('u-proud').value"));
    }
  }
  for (const p of [...ATS_PAGES.filter((x) => !x.file.endsWith("_form.html")).map((x) => ({ name: x.name, url: `https://${x.host}${x.path}` })),
    { name: "Workday", url: WD_INFO }]) {
    if (only && !only.test(p.name)) continue;
    await t.send("Page.navigate", { url: p.url });
    await t.until("document.readyState === 'complete'", 15000);
    await sleep(7000);
    const got = await look(p.name);
    if (AFTER) check(`${p.name}: fields are listed`, got.rows > 0, got);
  }
  check("no page error", !t.errors.length, t.errors);
} catch (err) {
  check("runner", false, String(err?.stack || err));
} finally {
  b.close();
}
console.log(JSON.stringify({ checks, measure }));

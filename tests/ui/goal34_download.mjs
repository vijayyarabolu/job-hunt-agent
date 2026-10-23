// GOAL34: Download PDF (the cover letter) and Download resume in the panel, on Workday, Greenhouse, Ashby, Lever and
// Dice fixture pages; Upload as PDF and Download PDF give the same bytes under the same name; Dice says why it gets
// Download instead of Upload; a cover button that finds nothing on the page says so; the Dice file card's label is
// its heading. The real extension, GOAL23's fake person; downloads land in the test's temp dir (never ~/Downloads).
// usage: node goal34_download.mjs <base> <token> <chrome> <profile dir> <extension dir> <fixtures dir>
import { existsSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { launch, serveFixtures, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXT_DIR, FIX] = process.argv.slice(2);
const WD = "zztest.wd5.myworkdayjobs.com";
const JOB = `https://${WD}/en-US/External/job/Foster-City-CA/AI-Engineer_JR0001`;
const SITES = join(FIX, "goal27", "sites");
const PAGES = [
  { name: "Greenhouse", host: "job-boards.greenhouse.io", url: "https://job-boards.greenhouse.io/zztest/jobs/27001", file: join(SITES, "greenhouse.html") },
  { name: "Ashby", host: "jobs.ashbyhq.com", url: "https://jobs.ashbyhq.com/zztest/27003/application", file: join(SITES, "ashby.html") },
  { name: "Lever", host: "jobs.lever.co", url: "https://jobs.lever.co/zztest/27002/apply", file: join(SITES, "lever.html") },
  { name: "Dice", host: "www.dice.com", url: "https://www.dice.com/job-applications/9bf99f47-zz-test/wizard", file: join(FIX, "dice", "wizard.html") },
];
const COVER = "Zz_Testcase_AI_Engineer_Cover_Letter.pdf";
const RESUME = "Zz_Testcase_AI_Engineer_Resume.pdf";
const DOWNLOADS = join(PROFILE_DIR, "downloads");
const checks = [];
const files = {};
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: typeof detail === "string" ? detail : JSON.stringify(detail) });
const listed = () => (existsSync(DOWNLOADS) ? readdirSync(DOWNLOADS) : []);

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
  // the extension's own world in the top frame (the latest one: each navigation makes a new one)
  const mainFrame = (await t.send("Page.getFrameTree")).frameTree.frame.id;
  let extCtx = null;
  b.on((m) => {
    if (m.sessionId !== t.sessionId || m.method !== "Runtime.executionContextCreated") return;
    const c = m.params.context;
    if (c.name === "Job Hunt Agent Autofill" && c.auxData && c.auxData.frameId === mainFrame && c.auxData.type === "isolated") extCtx = c.id;
  });
  const ext = async (expr) => (await t.send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, contextId: extCtx, awaitPromise: true, returnByValue: true })).result?.value;
  await serveFixtures(t, b, [WD, ...PAGES.map((p) => p.host)], (u) => {
    if (u.pathname === "/__sim/workday_sim.js") return { file: join(FIX, "workday_sim.js"), type: "text/javascript" };
    if (u.pathname === "/__sim/ats_sim.js") return { file: join(FIX, "goal27", "ats_sim.js"), type: "text/javascript" };
    if (u.hostname === WD) return { file: join(FIX, "workday", `${u.searchParams.get("fx") || "job"}.html`), type: "text/html" };
    const page = PAGES.find((p) => p.host === u.hostname && u.pathname === new URL(p.url).pathname);
    return page ? { file: page.file, type: "text/html" } : null;
  });
  const panelHas = async (text, ms = 10000) => { for (let i = 0; i < ms / 200; i++) { if ((await t.panelText()).includes(text)) return true; await sleep(200); } return false; };
  const landed = async (name, ms = 10000) => {
    for (let i = 0; i < ms / 100; i++) {
      if (listed().includes(name)) { await sleep(300); return readFileSync(join(DOWNLOADS, name)); }
      await sleep(100);
    }
    return null;
  };
  // the panel's Details (a <details>): opened by a person's click on its summary, the Download resume button is in it
  async function openDetails() {
    const { root } = await t.send("DOM.getDocument", { depth: -1, pierce: true });
    const find = (n) => { if (n.nodeName === "SUMMARY") return n; for (const k of [...(n.children || []), ...(n.shadowRoots || [])]) { const f = find(k); if (f) return f; } return null; };
    const s = find(root);
    return s ? t.clickNode(s.backendNodeId) : false;
  }
  async function coverReady() {
    await t.panelClick("Cover letter");
    return panelHas("Download PDF", 15000);
  }

  // Workday: the posting matches the job; Autofill starts this tab's session (the other boards' pages follow it)
  await t.send("Page.navigate", { url: `${JOB}?fx=job` });
  await t.until("document.readyState === 'complete' && window.__wd && window.__wd.loaded", 10000);
  check("Workday: the panel shows the matched job", await panelHas("ZZ Test Corp · AI Engineer"), await t.panelText());
  await t.panelClick("Autofill");
  check("Workday: Download PDF renders with Paste and Upload", await coverReady() && (await t.panelText()).includes("Upload as PDF"), await t.panelText());
  await openDetails();
  check("Workday: Download resume renders", await panelHas("Download resume"), await t.panelText());
  await t.panelClick("Download resume");
  const resume = await landed(RESUME);
  check("Download resume saves the tailored PDF under the base resume's name", resume && resume.subarray(0, 5).toString() === "%PDF-", listed());
  if (resume) files.resume = resume.toString("base64");
  check("the panel says where the resume went", await panelHas(`Downloaded ${RESUME}`), await t.panelText());

  for (const p of PAGES) {
    await t.send("Page.navigate", { url: p.url });
    await t.until("document.readyState === 'complete'", 10000);
    check(`${p.name}: the panel shows the job`, await panelHas("ZZ Test Corp · AI Engineer", 15000), await t.panelText());
    const ready = await coverReady();
    const text = await t.panelText();
    check(`${p.name}: Download PDF renders`, ready, text);
    await openDetails();
    check(`${p.name}: Download resume renders`, await panelHas("Download resume"), await t.panelText());
    check(`${p.name}: every cover letter button has its icon`, /↧\s*Paste into the form/.test(text) && /⤓\s*Download PDF/.test(text) &&
      (p.name === "Dice" || /⤒\s*Upload as PDF/.test(text)), text);

    if (p.name === "Greenhouse") {                      // its cover letter box takes the file: the same bytes both ways
      await t.panelClick("Upload as PDF");
      check("Greenhouse: Upload as PDF fills the cover letter box", await t.until("document.getElementById('cover_letter').files.length === 1", 10000));
      const up = await t.js(`const f = document.getElementById('cover_letter').files[0]; const b = new Uint8Array(await f.arrayBuffer());
        let s = ""; for (const x of b) s += String.fromCharCode(x); return { name: f.name, b64: btoa(s) }`);
      await t.panelClick("Download PDF");
      const down = await landed(COVER);
      check("Download PDF saves the cover letter under its upload name", down && up.name === COVER, [up.name, listed()]);
      check("Download PDF gives the same bytes as Upload as PDF", down && down.toString("base64") === up.b64, down ? down.length : "nothing landed");
      if (down) files.cover = down.toString("base64");
    }
    if (p.name === "Lever") {                           // no cover letter box here: the button says so, never silence
      await t.panelClick("Upload as PDF");
      check("Lever: Upload as PDF with no cover letter box says so",
        await panelHas("No cover letter upload found on this page: use Download PDF and drop it in", 15000), await t.panelText());
    }
    if (p.name === "Dice") {
      check("Dice: Upload as PDF is not offered", !text.includes("Upload as PDF"), text);
      check("Dice: the panel says why, with Download PDF right there",
        /Dice doesn't accept uploads from Autofill: use Download PDF and drop it in\s*↧\s*Paste into the form\s*⤓\s*Download PDF/.test(text), text);
      await t.panelClick("Paste into the form");
      check("Dice: Paste with no cover letter box says so", await panelHas("No cover letter box found on this page", 15000), await t.panelText());
      rmSync(join(DOWNLOADS, COVER), { force: true });   // the test's temp dir: Greenhouse's copy, so this one is seen landing
      await t.panelClick("Download PDF");
      const saved = await landed(COVER);
      check("Dice: Download PDF saves the cover letter", saved && saved.subarray(0, 5).toString() === "%PDF-", listed());
      const labels = await ext("return JH.fields.collect(document).filter((f) => f.kind === 'file').map((f) => f.label)");
      check("Dice: the cover letter file box is named by its heading", JSON.stringify(labels) === JSON.stringify(["Cover letter"]), labels);
      await t.panelClick("Autofill");
      await sleep(3000);
      const after = await t.panelText();
      check("Dice: the Skipped row reads Cover letter, not the whole card", !/OptionalUpload|letterFile types/.test(after) && /Cover letter/.test(after), after);
      check("Dice: nothing clicked Next or the drop zone", await t.js("return window.__dice.nextClicks === 0 && window.__dice.pickerOpened === 0"),
        await t.js("return window.__dice"));
    }
  }
  check("no page error", !t.errors.length, t.errors);
} catch (err) {
  check("runner", false, String(err?.stack || err));
} finally {
  b.close();
}
console.log(JSON.stringify({ checks, files }));

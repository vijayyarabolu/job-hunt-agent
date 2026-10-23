// GOAL33 §5: Save and Continue pressed while Autofill is still finishing a step. The next step is still auto-filled,
// and the first step's fields are logged under that step, not the next one. The real extension, GOAL23's fake person.
// usage: node goal33_ext.mjs <base> <token> <chrome> <profile dir> <extension dir> <fixtures dir>
import { join } from "node:path";
import { launch, serveFixtures, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXT_DIR, FIX] = process.argv.slice(2);
const HOST = "zztest.wd5.myworkdayjobs.com";
const JOB = `https://${HOST}/en-US/External/job/Foster-City-CA/AI-Engineer_JR0001`;
const APPLY = `${JOB}/apply/applyManually`;
const checks = [];
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: typeof detail === "string" ? detail : JSON.stringify(detail) });
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
  await serveFixtures(t, b, [HOST], (u) => {
    if (u.pathname === "/__sim/workday_sim.js") return { file: join(FIX, "workday_sim.js"), type: "text/javascript" };
    if (u.pathname.startsWith("/__sim/steps/")) return { file: join(FIX, "workday", "steps", u.pathname.split("/").pop()), type: "text/html" };
    return { file: join(FIX, "workday", `${u.searchParams.get("fx") || "job"}.html`), type: "text/html" };
  });
  const go = async (url) => { await t.send("Page.navigate", { url }); await t.until("document.readyState === 'complete' && window.__wd && window.__wd.loaded", 10000); };
  const panelHas = async (text, ms = 8000) => { for (let i = 0; i < ms / 200; i++) { if ((await t.panelText()).includes(text)) return true; await sleep(200); } return false; };
  await go(`${JOB}?fx=job`);
  await panelHas("ZZ Test Corp · AI Engineer");
  await t.panelClick("Autofill");
  check("the posting says click Apply", await panelHas("Click Apply on the posting"), await t.panelText());

  await go(`${APPLY}?fx=flow&step=info`);
  check("My Information starts filling", await t.until("document.getElementById('name--legalName--firstName')?.value", 20000));
  await t.clickSel("[data-automation-id=bottom-navigation-next-button]");          // at once, while it is still filling
  check("My Experience is still filled on its own", await t.until(`window.__wd.step === 'experience' && window.__wd.parsed &&
    document.getElementById('workExperience-2--companyName')?.value === 'Testworks'`, 90000),
    await t.js("return [window.__wd.step, [...document.querySelectorAll('[data-sec=work] input[id$=companyName]')].map((i) => i.value)]"));
  await sleep(1500);
} catch (err) {
  check("runner", false, String(err?.stack || err));
} finally {
  b.close();
}
console.log(JSON.stringify({ checks }));

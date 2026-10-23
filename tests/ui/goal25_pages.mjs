// GOAL25 §1/§3/§4 pages in headless Chrome: the notices when job-hunt-agent opens (the nightly check's drop until Got it,
// a newer extension with the user's own update steps), the Answers page's new parts (Learned + Remove, Company answers, Saved
// long answers, Stats, the typo fix, choice dropdowns, the education list, the Empty note leaving once saved) and
// Settings › Autofill › Health.
import { launch, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR] = process.argv.slice(2);
const checks = [];
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: typeof detail === "string" ? detail : JSON.stringify(detail) });
const S = JSON.stringify;

const b = await launch(CHROME, PROFILE_DIR);
let t;
try {
  t = await tab(b);
  const toastHas = (text, ms = 15000) => t.until(`[...document.querySelectorAll("#toasts .toast")].some((x) => x.textContent.includes(${S(text)}))`, ms);
  const toasts = () => t.js(`return document.getElementById("toasts").textContent`);
  const sec = (h2) => `[...document.querySelectorAll(".af-sec")].find((s) => s.querySelector("h2")?.textContent.startsWith(${S(h2)}))`;
  const secText = (h2) => t.js(`return (${sec(h2)})?.textContent || ""`);
  await t.send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#answers:tester` });

  // ---- when job-hunt-agent opens ----------------------------------------------------------------------------------------
  check("opening job-hunt-agent shows the nightly check's drop", await toastHas("Autofill's nightly check:") && await toastHas("greenhouse: apply page filled 4 of 12, down from 10 of 12"), await toasts());
  check("and that a newer extension is ready, with the user's update steps", await toastHas("is ready (yours is 1.0.0)") && await toastHas("chrome://extensions"), await toasts());
  await t.js(`const n = [...document.querySelectorAll("#toasts .toast")].find((x) => x.textContent.includes("nightly check:")); [...n.querySelectorAll("button")].find((x) => x.textContent === "Got it").click(); return true`);
  await sleep(600);
  check("Got it closes the drop notice", await t.js(`return ![...document.querySelectorAll("#toasts .toast")].some((x) => x.textContent.includes("nightly check:"))`));

  // ---- the Answers page ------------------------------------------------------------------------------------------
  await t.until("document.querySelector('#tabs [data-tab=answers]')", 10000);
  await t.js("document.querySelector('#tabs [data-tab=answers]').click(); return true");
  check("the Answers page shows Learned", await t.until(`!!(${sec("Learned (")})`, 15000), await t.js("return document.getElementById('view').textContent.slice(0, 300)"));
  const learned = await secText("Learned (");
  check("Learned says what, where and when", /notice period/i.test(learned) && learned.includes("greenhouse · Acme Robotics"), learned.slice(0, 400));
  check("Learned answers are editable", await t.js(`return [...(${sec("Learned (")}).querySelectorAll("input")].some((i) => i.value === "2 weeks")`));
  check("a company-only answer is marked so", learned.includes("this company only"), learned.slice(0, 400));
  const company = await secText("Company answers");
  check("Company answers are listed by company", company.includes("Acme Robotics") && /sponsorship/i.test(company), company.slice(0, 300));
  const snips = await secText("Saved long answers");
  check("the long answer is a saved long answer, not a usual answer", snips.startsWith("Saved long answers (1)") && snips.includes("robots in warehouses"), snips.slice(0, 300));
  const stats = await secText("Stats");
  check("Stats says how much Autofill filled by itself", /\d+% filled automatically/.test(stats) && stats.includes("Acme"), stats.slice(0, 300));
  const typo = await t.js(`return document.querySelector(".af-fixed")?.textContent || ""`);
  check("a typo in a choice answer is fixed and says so", typo.includes("“Hetrosexual” → “Heterosexual / Straight”"), typo);
  const choice = await t.js(`const s = [...document.querySelectorAll(".af-row select")].find((x) => /orientation/i.test(x.getAttribute("aria-label") || ""));
    return s ? { value: s.value, n: s.options.length } : null`);
  check("a choice question is a dropdown of common answers", choice && choice.value === "Heterosexual / Straight" && choice.n > 3, choice);
  await t.js(`[...(${sec("Education")}).querySelectorAll("button")].find((x) => x.textContent === "+ Add a degree").click(); return true`);
  check("Education: + Add a degree gives a row with School, Degree, Start and End", await t.js(`const s = ${sec("Education")};
    return ["School", "Degree", "Start MM/YYYY", "End MM/YYYY"].every((p) => s.querySelector(\`input[placeholder='\${p}']\`))`), await secText("Education"));
  const emptyRow = await t.js(`const r = [...document.querySelectorAll(".af-row")].find((x) => x.querySelector(".af-empty:not([hidden])") && x.querySelector("input[type=text]"));
    if (!r) return null; r.dataset.zz = "1"; return r.querySelector("span").textContent`);
  await t.js(`const i = document.querySelector(".af-row[data-zz] input[type=text]"); i.value = "ZZ test answer"; i.dispatchEvent(new Event("change")); return true`);
  check("the Empty note goes once the answer is saved", emptyRow && await t.until(`document.querySelector(".af-row[data-zz] .af-empty").hidden && document.querySelector(".af-row[data-zz] .saved-state").textContent === "Saved ✓"`, 8000), emptyRow);
  const before = await t.js(`return (${sec("Learned (")}).querySelectorAll("li").length`);
  await t.js(`[...(${sec("Learned (")}).querySelectorAll("li")].find((x) => /notice period/i.test(x.textContent)).querySelector("button").click(); return true`);
  check("Remove under Learned undoes it", await t.until(`(${sec("Learned (")})?.querySelectorAll("li").length === ${before - 1}`, 8000), await secText("Learned ("));
  await t.js(`[...(${sec("Learned (")}).querySelectorAll("li")].find((x) => /orientation/i.test(x.textContent)).querySelector("button").click(); return true`);
  await sleep(1500);
  const words = await t.js(`return [...document.querySelectorAll(".af-row select")].find((x) => /orientation/i.test(x.getAttribute("aria-label") || ""))?.value`);
  check("removing a typo fix puts the user's own words back, and they stay", words === "Hetrosexual" && !(await t.js(`return !!document.querySelector(".af-fixed")`)), words);

  // ---- Settings › Autofill › Health ------------------------------------------------------------------------------
  await t.js("document.getElementById('settings-btn').click(); return true");
  await t.until("!!document.querySelector('.set-nav [data-sec=autofill]')", 8000);
  await t.js("document.querySelector('.set-nav [data-sec=autofill]').click(); return true");
  const ready = await t.until(`document.querySelector(".af-health") && !document.querySelector(".af-health").textContent.includes("Loading")`, 10000);
  const hh = await t.js(`return document.querySelector(".af-health")?.textContent || ""`);
  check("Health shows the fill rate per board", ready && /greenhouse\s*8/.test(hh) && /\d+%/.test(hh), hh.slice(0, 400));
  check("Health shows errors by board and step", hh.includes("workday · questions1") && hh.includes("boom (test)"), hh.slice(0, 600));
  check("Health shows the nightly check", hh.includes("▲ 1 page(s) dropped"), hh.slice(0, 800));
  check("Health shows the version and the update steps", hh.includes("Your Chrome uses 1.0.0") && hh.includes("Update ready"), hh.slice(0, 300));
  check("no page errors", t.errors.length === 0, t.errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
} finally {
  b.close();
  console.log(JSON.stringify({ checks }));
}

// GOAL29 §4: Workday's Skills box on a tenant that clears the box slowly after each pick and saves free text on Enter
// (IRC, 2026-10-19: "hain", "ve AI", "Smith", "loudWatch"). The extension's content scripts run in a plain page with
// the Workday simulator; the board's own Skills step types the list; every pick must read exactly the skill wanted.
// argv: chromePath profileDir extensionDir fixturesDir. Prints {"checks": [...]}.
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { start } from "./cdp.mjs";

const [chromePath, profileDir, EXT, FIX] = process.argv.slice(2);
const SCRIPTS = ["content/core.js", "content/pick.js", "content/classify.js", "content/fields.js", "content/fill.js", "content/boards/workday.js"];
const page = (step) => `<!doctype html><html><head><meta charset="utf-8"></head><body><main><div data-automation-id="applyFlowPage"></div></main>
${SCRIPTS.map((s) => `<script src="/ext/${s}"></script>`).join("")}<script>history.replaceState(null, "", "?step=${step}")</script><script src="/__sim/workday_sim.js"></script></body></html>`;
const server = createServer((req, res) => {
  const u = new URL(req.url, "http://x");
  let body, type = "text/html";
  try {
    if (u.pathname.startsWith("/ext/")) { body = readFileSync(join(EXT, u.pathname.slice(5))); type = "text/javascript"; }
    else if (u.pathname === "/__sim/workday_sim.js") { body = readFileSync(join(FIX, "workday_sim.js")); type = "text/javascript"; }
    else if (u.pathname.startsWith("/__sim/steps/")) body = readFileSync(join(FIX, "workday", "steps", u.pathname.split("/").pop()));
    else body = page(u.searchParams.get("p") || "skills_slow");
  } catch { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { "Content-Type": type });
  res.end(body);
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;
const t = await start(chromePath, profileDir);
const chips = () => t.js("return [...document.querySelectorAll('[data-automation-id=formField-skills] [data-automation-id=selectedItem]')].map((x) => x.title)");
async function run(step, list) {
  await t.send("Page.enable");
  await t.send("Page.navigate", { url: `${base}/?p=${step}` });
  await t.until("window.__wd && window.__wd.loaded && document.querySelector('[data-automation-id=formField-skills] input')", 15000);
  return t.js(`const b = JH.boards.find((x) => x.id === "workday"); const out = { filled: 0, notes: [] };
    await b.addSkills(${JSON.stringify(list)}, out); return out;`);
}
try {
  // 1. picks from the list and free text, each typed only once the box is empty again
  const want1 = ["Python", "LangChain", "LangSmith", "CloudWatch", "Generative AI", "Kubernetes"];
  const out1 = await run("skills_slow", want1);
  const got1 = await chips();
  t.check("slow-clearing box: every pick reads exactly the skill (no cut first letters)",
    JSON.stringify(got1) === JSON.stringify(["Amazon Web Services (AWS)", ...want1]), JSON.stringify({ got1, out1 }));
  t.check("slow-clearing box: the panel note counts them all", /Skills: 6 added/.test((out1 || {}).notes?.join(" ")), JSON.stringify(out1));
  // 2. a late clear mid-typing is caught before Enter (retyped); a pick saved wrong is removed and reported
  const out2 = await run("skills_wipe", ["LangSmith", "CloudWatch", "Python"]);
  const got2 = await chips();
  t.check("a box wiped while typing is retyped before Enter", got2.includes("LangSmith") && !got2.some((c) => /^\w*Smith$/.test(c) && c !== "LangSmith"), JSON.stringify({ got2, out2 }));
  t.check("a pick saved wrong is removed", !got2.includes("loudWatch") && got2.includes("Python"), JSON.stringify(got2));
  t.check("and reported in the panel notes", /could not add cleanly: CloudWatch/.test((out2 || {}).notes?.join(" ")), JSON.stringify(out2));
  t.check("no page errors", !t.errors.length, t.errors.join(" | "));
} catch (e) {
  t.check("runner", false, e.stack || String(e));
} finally {
  t.close();
  server.close();
}
console.log(JSON.stringify({ checks: t.checks }));

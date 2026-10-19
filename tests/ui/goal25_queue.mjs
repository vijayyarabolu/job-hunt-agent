// "Apply on laptop" in headless Chrome with the real extension. The phone (390 px, touch): a job's
// "On laptop" button and a row swiped left queue them; the laptop: "Apply queue (2)" in the top bar, Open next 5 opens
// both apply pages in new tabs (the extension opens them and tells each tab its job), Mark applied takes one out;
// Jordan's queue is her own.
import { launch, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXT_DIR] = process.argv.slice(2);
const checks = [];
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: typeof detail === "string" ? detail : JSON.stringify(detail) });
const api = async (path, pid, body) => (await fetch(`${BASE}${path}?profile=${pid}`, { method: body ? "POST" : "GET",
  headers: { "X-Jobhunt": "1", Cookie: `jh_token=${TOKEN}`, "Content-Type": "application/json" }, body: body ? JSON.stringify({ profile: pid, ...body }) : undefined })).json();
const toastHas = (t, text, ms = 6000) => t.until(`[...document.querySelectorAll("#toasts .toast")].some((x) => x.textContent.includes(${JSON.stringify(text)}))`, ms);
const clickText = (t, sel, text) => t.js(`const b = [...document.querySelectorAll(${JSON.stringify(sel)})].find((x) => x.textContent.trim().includes(${JSON.stringify(text)}) && x.offsetParent);
  if (b) b.click(); return !!b;`);

const b = await launch(CHROME, PROFILE_DIR);
try {
  const { id } = await b.send("Extensions.loadUnpacked", { path: EXT_DIR });
  const o = await tab(b, `chrome-extension://${id}/options.html`);
  await o.until("document.getElementById('base')");
  await o.js(`const $ = (i) => document.getElementById(i); $("base").value = ${JSON.stringify(BASE)}; $("token").value = ${JSON.stringify(TOKEN)};
    if (!document.querySelector("input[name=profile][value=tester]")) document.querySelector(".seg").append(Object.assign(document.createElement("input"), { type: "radio", name: "profile", value: "tester" }));
    document.querySelector("input[name=profile][value=tester]").checked = true; if ($("auto")) $("auto").checked = false;
    $("form").requestSubmit(); return true`);
  await sleep(1500);

  // ---- the phone: On laptop, then a swipe ------------------------------------------------------------------------
  const p = await tab(b);
  await p.send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await p.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  await p.send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:tester` });
  check("phone: the feed shows jobs", await p.until("document.querySelectorAll('#list li.row[id^=job-]').length >= 3", 20000), await p.js("return document.title"));
  await p.js("document.getElementById('job-0').click(); return true");
  const onLaptop = await p.until(`[...document.querySelectorAll(".acts .lbl-btn")].some((x) => x.textContent.includes("On laptop") && x.offsetParent)`, 10000);
  check("phone: the job has an On laptop button", onLaptop, await p.js(`return [...document.querySelectorAll(".acts .lbl-btn")].map((x) => x.textContent).join(" | ")`));
  await clickText(p, ".acts .lbl-btn", "On laptop");
  check("phone: On laptop says it is queued", await toastHas(p, "Apply queue (1)"), await p.js(`return document.getElementById("toasts").textContent`));
  await p.send("Page.reload");
  await p.until("document.querySelectorAll('#list li.row[id^=job-]').length >= 3", 20000);
  await sleep(1500);                                     // the queue module loads after the first paint
  const r = await p.js(`const r = document.getElementById("job-1").getBoundingClientRect(); return { y: r.top + r.height / 2, w: innerWidth }`);
  const touch = (type, x) => p.send("Input.dispatchTouchEvent", { type, touchPoints: type === "touchEnd" ? [] : [{ x, y: r.y }] });
  await touch("touchStart", r.w - 40);
  for (const x of [r.w - 90, r.w - 160, r.w - 230]) { await touch("touchMove", x); await sleep(30); }
  await p.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  check("phone: a row swiped left is queued", await toastHas(p, "Apply queue (2)"), await p.js(`return document.getElementById("toasts").textContent`));
  check("phone: the top bar has no queue button", await p.js("return !document.getElementById('applyq-btn')"));
  const q = await api("/api/applyq", "tester");
  check("two jobs wait in the queue", q.count === 2, q);

  // ---- the laptop: Apply queue (2), Open next 5, Mark applied --------------------------------------------------------
  const l = await tab(b);
  await l.send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:tester` });
  const shown = await l.until(`document.getElementById("applyq-btn") && !document.getElementById("applyq-btn").hidden && document.getElementById("applyq-btn").textContent.includes("Apply queue (2)")`, 20000);
  check("laptop: the top bar says Apply queue (2)", shown, await l.js(`return document.getElementById("applyq-btn")?.outerHTML || "none"`));
  await l.js("document.getElementById('applyq-btn').click(); return true");
  await l.until("document.getElementById('applyq')?.open && document.querySelectorAll('#applyq .q-row').length === 2", 8000);
  const before = new Set((await b.send("Target.getTargets")).targetInfos.map((x) => x.targetId));
  await clickText(l, "#applyq .row-btns .btn", "Open next 5");
  let opened = [];
  for (let i = 0; i < 30 && opened.length < 2; i++) {
    await sleep(300);
    opened = (await b.send("Target.getTargets")).targetInfos.filter((x) => x.type === "page" && !before.has(x.targetId) && x.url.includes("/zz-apply/"));
  }
  check("laptop: Open next 5 opens both apply pages in new tabs", opened.length === 2, opened.map((x) => x.url));
  check("laptop: it says the tabs know their jobs", await toastHas(l, "Autofill knows each one's job"), await l.js(`return document.getElementById("toasts").textContent`));
  const sw = (await b.send("Target.getTargets")).targetInfos.find((x) => x.type === "service_worker" && x.url.startsWith(`chrome-extension://${id}/`));
  const { sessionId } = await b.send("Target.attachToTarget", { targetId: sw.targetId, flatten: true });
  const stored = await b.send("Runtime.evaluate", { expression: "chrome.storage.session.get(null)", awaitPromise: true, returnByValue: true }, sessionId);
  const jobs = Object.entries(stored.result.value || {}).filter(([k]) => k.startsWith("tab:")).map(([, v]) => v.job && v.job.uid).filter(Boolean);
  check("each new tab knows its job", q.items.every((x) => jobs.includes(x.uid)), { jobs, queued: q.items.map((x) => x.uid) });
  await clickText(l, "#applyq .q-row .btn", "Mark applied");
  check("laptop: Mark applied takes it out", await l.until(`document.getElementById("applyq-btn").textContent.includes("Apply queue (1)")`, 8000),
    await l.js(`return document.getElementById("applyq-btn").textContent`));

  // ---- Jordan has her own queue ---------------------------------------------------------------------------------------
  const theirs = await api("/api/applyq/add", "tester2", { uid: q.items[0].uid });
  check("Jordan: her queue starts with her own job", theirs.count === 1, theirs);
  await l.js("document.getElementById('applyq').close(); document.querySelector('#who [data-person=tester2]').click(); return true");
  check("Jordan: switching to her shows her queue (1)", await l.until(`document.getElementById("applyq-btn").textContent.includes("Apply queue (1)")`, 8000),
    await l.js(`return document.getElementById("applyq-btn").textContent`));
  check("no page errors", !p.errors.length && !l.errors.length, [...p.errors, ...l.errors].join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
} finally {
  b.close();
  console.log(JSON.stringify({ checks }));
}

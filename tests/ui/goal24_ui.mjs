// GOAL24 in headless Chrome (tests/test_goal24_ui.py): the H-1B sponsorship badges for a red-green colour-blind user
// (icon shape + word on every badge, blue / grey / red only, readable under deuteranopia and protanopia simulation),
// on the feed, the job panel, the Tracker, the phone and the Autofill extension's panel; the Recommended switch with
// its counts; No sponsorship shown, last; "Load more" past the first page with no cap; the Answers-page setting; and
// the extension filling Yes to the future-sponsorship question at a sponsor (the saved answer once the setting is Off).
// usage: node goal24_ui.mjs <base> <token> <chrome> <profile dir> <extension dir> <form fixture> <shots dir>
import { writeFileSync } from "node:fs";
import { launch, serveFixtures, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXT_DIR, FORM, SHOTS] = process.argv.slice(2);
const PID = "zztest";
const HOST = "careers.zztest-sponsor.example";
const JOB_URL = `https://${HOST}/jobs/acme-1`;
const S = JSON.stringify;
const checks = [];
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: typeof detail === "string" ? detail : S(detail) });
const api = async (path, body) => {
  const r = await fetch(`${BASE}${path}${path.includes("?") ? "&" : "?"}profile=${PID}`, { method: body ? "POST" : "GET",
    headers: { "X-Jobhunt-Token": TOKEN, "X-Jobhunt": "1", "Content-Type": "application/json" }, body: body ? S({ profile: PID, ...body }) : undefined });
  return r.json();
};
const WORDS = { sponsors: ["check", "Sponsors · 3 in 12 mo"], cap_exempt: ["star", "Cap-exempt"], maybe: ["qmark", "Sponsored before"],
  no: ["x", "No sponsorship"] };
// Machado et al. 2009, severity 1 (the model Chrome's vision-deficiency emulation uses)
const SIM = { deuteranopia: [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.01182, 0.04294, 0.968881]],
  protanopia: [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]] };
const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
const gam = (c) => Math.round(255 * Math.min(1, Math.max(0, c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055)));
const simulate = (rgb, m) => { const l = rgb.map(lin); return m.map((row) => gam(row[0] * l[0] + row[1] * l[1] + row[2] * l[2])); };
const lum = (rgb) => { const [r, g, b] = rgb.map(lin); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const greenish = ([r, g, bl]) => g > r + 30 && g > bl + 30;
const rgb = (css) => (String(css).match(/[\d.]+/g) || []).slice(0, 3).map(Number);
const SOLID = ["sponsors", "no"], OUTLINE = ["cap_exempt", "maybe"];

const b = await launch(CHROME, PROFILE_DIR);
let t;
try {
  const w = await tab(b);
  const shot = async (tb, name) => { const r = await tb.send("Page.captureScreenshot", { format: "png" }); writeFileSync(`${SHOTS}/${name}.png`, Buffer.from(r.data, "base64")); };
  const size = (width, height, mobile = false) => w.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: mobile ? 3 : 1, mobile });
  let loads = 0;
  const load = async (hash) => {
    await w.send("Page.navigate", { url: `${BASE}/?token=${TOKEN}&n=${++loads}#${hash}` });
    return w.until("document.querySelectorAll('#list .row[data-i]').length > 0 && !document.querySelector('#list[aria-busy]')", 20000);
  };
  const rows = () => w.js("return [...document.querySelectorAll('#list .row[data-i]')].map((r) => ({ co: r.querySelector('.co')?.textContent, sb: r.querySelector('.sb')?.className || null }))");
  const badges = (scope) => w.js(`return [...document.querySelectorAll(${S(scope)})].map((s) => { const cs = getComputedStyle(s);
    let p = s.parentElement; while (p && /rgba\\(0, 0, 0, 0\\)|transparent/.test(getComputedStyle(p).backgroundColor)) p = p.parentElement;
    return { cls: s.className, text: s.textContent.trim(), icon: s.querySelector('use')?.getAttribute('href'), title: s.title,
      bg: cs.backgroundColor, border: cs.borderTopColor, color: cs.color, behind: p ? getComputedStyle(p).backgroundColor : 'rgb(255, 255, 255)',
      seen: s.getBoundingClientRect().width > 0 && s.getBoundingClientRect().right <= innerWidth + 1 }; })`);

  await size(1440, 900);
  await w.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });   // the Mac may be in dark mode
  check("the feed loads", await load(`recommended:${PID}`), await w.js("return document.body.innerText.slice(0, 300)"));

  // ---- the badges on the first page: shape + word, never colour alone ----
  const all = await badges("#list .row .sb");
  for (const kind of ["sponsors", "cap_exempt"]) {
    const [icon, word] = WORDS[kind];
    const found = all.find((x) => x.cls.includes(`sb-${kind}`));
    check(`a ${kind} badge has its icon and word`, found && found.icon === `#i-${icon}` && found.text === word, found || all.slice(0, 4));
  }
  const acme = all.find((x) => x.cls.includes("sb-sponsors"));
  check("hover shows the evidence", acme && /3 H-1B filings in the last 12 months \(3 data \/ AI\)/.test(acme.title) && /wage level II/i.test(acme.title), acme?.title);
  const first = await rows();
  check("sponsors lead the day", first.slice(0, 2).every((r) => /sb-(sponsors|cap_exempt)/.test(r.sb || "")), first.slice(0, 4));
  check("a job with nothing known has no badge", first.some((r) => (r.co || "").startsWith("ZZ Filler") && r.sb === null), first.slice(2, 5));
  check("the first page is 300 jobs", first.length === 300, first.length);

  // ---- Load more: the next page by itself on scroll, no cap; No sponsorship shown, last ----
  await w.js("const l = document.querySelector('#list-wrap'); l.scrollTop = l.scrollHeight");
  if (!(await w.until("document.querySelectorAll('#list .row[data-i]').length >= 305", 15000))) await w.js("document.querySelector('#list-more .more-btn')?.click()");
  await w.until("document.querySelectorAll('#list .row[data-i]').length >= 305", 8000);
  const after = await rows();
  check("Load more brings the rest (no cap)", after.length === 305, after.length);
  check("No sponsorship is shown, ranked last", after.at(-1)?.co === "Globex" && (after.at(-1)?.sb || "").includes("sb-no"), after.slice(-3));
  check("Sponsored before comes after the unknowns of its day", after.at(-2)?.co === "Initech" && (after.at(-2)?.sb || "").includes("sb-maybe"), after.slice(-3));
  const every = await badges("#list .row .sb");
  for (const kind of ["maybe", "no"]) {
    const [icon, word] = WORDS[kind];
    const found = every.find((x) => x.cls.includes(`sb-${kind}`));
    check(`a ${kind} badge has its icon and word`, found && found.icon === `#i-${icon}` && found.text === word, found);
  }
  const kinds = Object.fromEntries(Object.keys(WORDS).map((k) => [k, every.find((x) => x.cls.includes(`sb-${k}`))]));
  const paint = (k) => (SOLID.includes(k) ? rgb(kinds[k].bg) : rgb(kinds[k].border));
  check("no badge is green", Object.keys(WORDS).every((k) => kinds[k] && !greenish(paint(k)) && !greenish(rgb(kinds[k].color))),
    Object.keys(WORDS).map((k) => kinds[k] && [k, kinds[k].bg, kinds[k].border, kinds[k].color]));
  check("Sponsors is solid blue, No sponsorship solid red", rgb(kinds.sponsors?.bg).join() === "31,95,191" && rgb(kinds.no?.bg).join() === "180,35,24",
    [kinds.sponsors?.bg, kinds.no?.bg]);
  const readable = (m) => [...SOLID.map((k) => [k, contrast(simulate(rgb(kinds[k].bg), m), simulate(rgb(kinds[k].color), m))]),
    ...OUTLINE.map((k) => [k, contrast(simulate(rgb(kinds[k].color), m), simulate(rgb(kinds[k].bg === "rgba(0, 0, 0, 0)" ? kinds[k].behind : kinds[k].bg), m))])];
  check("every badge's word is readable (4.5:1)", readable([[1, 0, 0], [0, 1, 0], [0, 0, 1]]).every(([, c]) => c >= 4.5), readable([[1, 0, 0], [0, 1, 0], [0, 0, 1]]));
  for (const [name, m] of Object.entries(SIM)) {
    const got = readable(m);
    check(`${name}: every badge's word stays readable (4.5:1)`, got.every(([, c]) => c >= 4.5), got.map(([k, c]) => [k, c.toFixed(2)]));
    const [sp, no] = SOLID.map((k) => simulate(rgb(kinds[k].bg), m));
    check(`${name}: Sponsors and No sponsorship still look different`, dist(sp, no) > 100, [sp, no]);
  }
  check("the four badges differ by icon and word", new Set(Object.values(kinds).map((x) => x && `${x.icon}|${x.text}`)).size === 4);

  // ---- colour-blind simulation screenshots of the feed (light and dark) ----
  await w.js("document.querySelector('#list-wrap').scrollTop = 0");
  await sleep(300);
  await shot(w, "feed-light");
  for (const type of ["deuteranopia", "protanopia"]) {
    await w.send("Emulation.setEmulatedVisionDeficiency", { type });
    await sleep(300);
    await shot(w, `feed-${type}`);
  }
  await w.send("Emulation.setEmulatedVisionDeficiency", { type: "none" });
  await w.js("document.documentElement.dataset.theme = 'dark'");
  await sleep(300);
  const darkSp = (await badges("#list .row .sb")).find((x) => x.cls.includes("sb-sponsors"));
  check("dark mode: the Sponsors word is readable", darkSp && contrast(rgb(darkSp.bg), rgb(darkSp.color)) >= 4.5, darkSp && [darkSp.bg, darkSp.color]);
  await shot(w, "feed-dark");
  await w.send("Emulation.setEmulatedVisionDeficiency", { type: "deuteranopia" });
  await sleep(300);
  await shot(w, "feed-dark-deuteranopia");
  await w.send("Emulation.setEmulatedVisionDeficiency", { type: "none" });
  await w.js("delete document.documentElement.dataset.theme");

  // ---- the switch: counts on each, the list follows, remembered per person ----
  const seg = () => w.js("return [...document.querySelectorAll('.sb-seg button')].map((x) => [x.dataset.spons, x.textContent.trim(), x.getAttribute('aria-pressed')])");
  const s0 = await seg();
  check("the switch shows counts", S(s0.map(([v, txt]) => [v, txt])) === S([["friendly", "Sponsor-friendly 3"], ["no", "No sponsorship 1"], ["", "All 305"]]), s0);
  await w.js("document.querySelector('.sb-seg [data-spons=no]').click()");
  await w.until("document.querySelectorAll('#list .row[data-i]').length === 1", 8000);
  check("No sponsorship lists only those", S((await rows()).map((r) => r.co)) === S(["Globex"]), await rows());
  await w.js("document.querySelector('.sb-seg [data-spons=friendly]').click()");
  await w.until("document.querySelectorAll('#list .row[data-i]').length === 3", 8000);
  const friendly = await rows();
  check("Sponsor-friendly lists Sponsors, Cap-exempt and Sponsored before (no Unknown)", friendly.length === 3 && friendly.every((r) => /sb-(sponsors|cap_exempt|maybe)/.test(r.sb || "")), friendly);
  await load(`recommended:${PID}`);
  check("the switch is remembered", (await w.until("document.querySelector('.sb-seg [data-spons=friendly][aria-pressed=true]')", 5000)) && (await rows()).length === 3, await seg());
  await w.js("document.querySelector('.sb-seg [data-spons=\"\"]').click()");
  await w.until("document.querySelectorAll('#list .row[data-i]').length >= 300", 8000);

  // ---- the job panel: badge in the header, the evidence block ----
  const at = await w.js("const r = [...document.querySelectorAll('#list .row[data-i]')].find((x) => x.querySelector('.co')?.textContent === 'Acme Robotics'); r.scrollIntoView({block: 'center'}); const b = r.querySelector('.row-main').getBoundingClientRect(); return [b.left + 30, b.top + 10]");
  await w.clickAt(at[0], at[1]);
  const panel = await w.until("document.querySelector('.d-co .sb-sponsors') && document.querySelectorAll('.sb-block .sb-ev li').length >= 2", 10000);
  check("the job panel shows the badge and its evidence", panel, await w.js("return document.querySelector('.sb-block')?.innerText || document.querySelector('.d-co')?.innerText || ''"));
  await shot(w, "panel");
  await w.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });

  // ---- the Tracker ----
  await w.js("document.querySelector('#tabs [data-tab=tracker]').click()");
  const tracked = await w.until("[...document.querySelectorAll('.sb.sb-sponsors')].some((s) => s.offsetParent && /Sponsors/.test(s.textContent) && !s.closest('#list .row-meta, .d-co, .sb-block'))", 10000);
  check("a Tracker row shows its company's badge, icon and word", tracked, await w.js("return (document.querySelector('#view') || document.body).innerText.slice(0, 400)"));
  await shot(w, "tracker");

  // ---- Answers › Legal: the setting, On by default ----
  await w.js("document.querySelector('#tabs [data-tab=answers]').click()");
  const box = await w.until("document.querySelector('.af-toggle input[type=checkbox]')", 10000);
  check("Answers shows the sponsorship setting, On", box && await w.js("return document.querySelector('.af-toggle input').checked && /Sponsors or Cap-exempt/.test(document.querySelector('.af-toggle').innerText)"),
    box ? await w.js("return document.querySelector('.af-toggle').innerText") : "no setting");
  await w.js("document.querySelector('.af-toggle input').click()");
  await w.until("/Saved ✓ \\(Off\\)/.test(document.querySelector('.af-toggle .saved-state')?.textContent || '')", 5000);
  const saved = (await api("/api/autofill/answers")).bank?.find((r) => r.key === "sponsor_auto");
  check("turning it off is saved", saved?.answer === "Off", saved);
  await w.js("document.querySelector('.af-toggle input').click()");
  await w.until("/Saved ✓ \\(On\\)/.test(document.querySelector('.af-toggle .saved-state')?.textContent || '')", 5000);
  await shot(w, "answers");

  // ---- the phone ----
  await size(390, 844, true);
  await load(`recommended:${PID}`);
  const phone = await badges("#list .row .sb");
  check("phone: badges show with their word", phone.filter((x) => x.seen && x.text).length >= 2, phone.slice(0, 3));
  check("phone: the switch fits", await w.js("const s = document.querySelector('.sb-seg'); return !!s && s.getBoundingClientRect().right <= innerWidth + 1"));
  check("phone: no sideways scroll", await w.js("return document.scrollingElement.scrollWidth <= innerWidth && document.body.scrollWidth <= innerWidth"));
  await shot(w, "phone");
  await w.send("Emulation.setEmulatedVisionDeficiency", { type: "protanopia" });
  await sleep(300);
  await shot(w, "phone-protanopia");
  await w.send("Emulation.setEmulatedVisionDeficiency", { type: "none" });
  check("no page errors (web UI)", w.errors.length === 0, w.errors.join(" | "));

  // ---- the Autofill extension: the badge and the answer it will use, before filling ----
  const { id } = await b.send("Extensions.loadUnpacked", { path: EXT_DIR });
  const o = await tab(b, `chrome-extension://${id}/options.html`);
  await o.until("document.getElementById('base') && document.readyState === 'complete'");
  await sleep(1000);                                     // its saved settings load first, then ours go in
  await o.js(`const $ = (i) => document.getElementById(i);
    $("base").value = ${S(BASE)}; $("token").value = ${S(TOKEN)};
    document.querySelector("input[name=profile][value=${PID}]") || document.querySelector(".seg").append(Object.assign(document.createElement("input"), {type: "radio", name: "profile", value: "${PID}"}));
    document.querySelector("input[name=profile][value=${PID}]").checked = true;
    $("email").value = "zz.sponsor@example.com"; $("password").value = "Zz-Test-Pass-1"; $("test").click()`);
  check("the extension connects", await o.until(`document.getElementById('test-out').textContent.startsWith('Connected as ${PID}')`, 20000),
    [await o.js("return document.getElementById('test-out').textContent"), o.errors, o.logs]);
  t = await tab(b);
  await serveFixtures(t, b, [HOST], () => ({ file: FORM, type: "text/html" }));
  const panelHas = async (text, ms = 10000) => { for (let i = 0; i < ms / 200; i++) { if ((await t.panelText()).includes(text)) return true; await sleep(200); } return false; };
  const open = async () => { await t.send("Page.navigate", { url: JOB_URL }); await t.until("document.readyState === 'complete'", 10000); await sleep(1200); };
  const radio = (name) => t.js(`return document.querySelector('input[name=${name}]:checked')?.value || null`);
  await open();
  check("the panel shows the job's badge (icon + word)", await panelHas("✓ Sponsors · 3 in 12 mo"), await t.panelText());
  check("the panel says which answer it will fill", await panelHas("Future sponsorship question: Yes (this company sponsors"), await t.panelText());
  await shot(t, "extension-panel");
  await t.panelClick("Autofill");
  await t.until("document.querySelector('input[name=future]:checked') && document.querySelector('input[name=now]:checked')", 15000);
  check("at a sponsor the future question gets Yes", (await radio("future")) === "Yes", await radio("future"));
  check("the need-it-now question keeps the saved answer", (await radio("now")) === "No", await radio("now"));
  await api("/api/autofill/answer", { key: "sponsor_auto", answer: "Off" });
  await open();
  check("with the setting Off the panel says the saved answer", await panelHas("Future sponsorship question: No (your saved answer)"), await t.panelText());
  await t.panelClick("Autofill");
  await t.until("document.querySelector('input[name=future]:checked')", 15000);
  check("with the setting Off the future question gets the saved answer", (await radio("future")) === "No", await radio("future"));
  await api("/api/autofill/answer", { key: "sponsor_auto", answer: "On" });
  check("the form is never submitted", (await t.js("return window.__submitted")) === 0);
  check("no page errors (extension)", t.errors.length === 0, t.errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
} finally {
  console.log(S({ checks }));
  b.close();
}

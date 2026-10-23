// Text contrast of every button and input, in every state (normal, hover, focus, pressed, disabled), light and dark:
// the job-hunt-agent site (every tab, Settings sections, a job's panel, the Not interested "Why?" box with its own reason
// box), the extension's options page, and the Autofill panel (normal and error state) on a saved Ashby page and on a
// page whose CSS tries to restyle it. WCAG AA: 4.5:1 for text, 3:1 for an icon-only button. Contrast, not colour,
// is what makes a control readable with colour blindness.
// usage: node contrast.mjs <base> <token> <chrome> <profile dir> <extension dir> <fixtures dir> <hostile page>
import { writeFileSync } from "node:fs";
import { join } from "node:path";
import { launch, serveFixtures, sleep, tab } from "./extpipe.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, EXT_DIR, FIX, HOSTILE] = process.argv.slice(2);
const TEXT_MIN = 4.5;
const ICON_MIN = 3;
const MAX_NODES = 500;
const STATES = ["normal", "hover", "focus", "active", "disabled"];
const NO_MOTION = "*, *::before, *::after { transition: none !important; animation: none !important; caret-color: auto !important }";
const SHOTS = process.env.JH_CONTRAST_SHOTS || "";     // a folder: screenshots of the boxes the user named
const checks = [];
const failures = [];
const check = (name, ok, detail = "") => checks.push({ name, ok: !!ok, detail: typeof detail === "string" ? detail : JSON.stringify(detail) });

// runs on one control (this): its text colour against what is really behind it (backgrounds composited up to the
// page, crossing shadow roots, with opacity), as WCAG's contrast ratio
const MEASURE = `function () {
  const cv = (globalThis.__jhCv ||= Object.assign(document.createElement("canvas"), { width: 1, height: 1 }).getContext("2d", { willReadFrequently: true }));
  const rgba = (c) => { cv.clearRect(0, 0, 1, 1); cv.fillStyle = "rgba(0,0,0,0)"; cv.fillStyle = c; cv.fillRect(0, 0, 1, 1);
    const d = cv.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const over = (top, a, under) => [0, 1, 2].map((i) => top[i] * a + under[i] * (1 - a));
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const chain = [];
  for (let n = this; n; n = n.parentNode || n.host) if (n instanceof Element) chain.unshift(n);
  const filtered = chain.some((n) => { const st = getComputedStyle(n); return st.filter !== "none" || st.mixBlendMode !== "normal"; });
  let bg = [255, 255, 255], op = 1;
  for (const n of chain) {
    const s = getComputedStyle(n);
    op *= Number(s.opacity);
    const c = rgba(s.backgroundColor);
    if (c[3] > 0) bg = over(c, c[3] * op, bg);
  }
  const s = getComputedStyle(this);
  const value = "value" in this && this.tagName !== "BUTTON" ? String(this.value || "") : "";
  const text = (this.tagName === "SELECT" ? (this.selectedOptions[0]?.text || "") : value || (this.innerText || "")).trim();
  const ph = !value && this.getAttribute("placeholder") ? this.getAttribute("placeholder") : "";
  const fgOf = (c) => { const v = rgba(c); return over(v, v[3] * op, bg); };
  const ratio = (fg) => { const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x); return Math.round(((a + 0.05) / (b + 0.05)) * 100) / 100; };
  const fg = fgOf(s.color);
  const icon = !text && !ph && !!this.querySelector("svg, img, .ic");
  const ctx = [];
  for (let n = this.parentNode || this.host, i = 0; n && i < 4; n = n.parentNode || n.host, i++)
    if (n instanceof Element && (n.id || n.classList.length)) ctx.push(n.id ? "#" + n.id : "." + [...n.classList].slice(0, 2).join("."));
  const what = this.tagName.toLowerCase() + (this.id ? "#" + this.id : "") + (this.classList.length ? "." + [...this.classList].slice(0, 3).join(".") : "")
    + (this.type && this.tagName === "INPUT" ? "[" + this.type + "]" : "") + " '" + (text || ph || this.getAttribute("aria-label") || this.title || "").slice(0, 40) + "'"
    + (ctx.length ? " in " + ctx.join(" < ") : "");
  const out = { what, fg: "rgb(" + fg.map(Math.round) + ")", bg: "rgb(" + bg.map(Math.round) + ")", ratio: ratio(fg), need: icon ? ${ICON_MIN} : ${TEXT_MIN},
    filtered, empty: !text && !ph && !icon, sig: what.replace(/'[^']*'/, "") + "|" + s.color + "|" + s.backgroundColor + "|" + bg.map(Math.round) };
  if (ph) { const p = fgOf(getComputedStyle(this, "::placeholder").color); out.ph = { fg: "rgb(" + p.map(Math.round) + ")", ratio: ratio(p) }; }
  return out;
}`;
// on screen: a size, not hidden, and not faded out with its toast or dialog (opacity counted up the tree); a toast on
// its way out (.toast.out) is no control (GOAL33: the full suite caught one half-gone)
const VISIBLE = `function () { const r = this.getBoundingClientRect(); if (r.width <= 2 || r.height <= 2 || getComputedStyle(this).visibility === "hidden") return false;
  if (this.closest(".toast.out")) return false;
  let op = 1; for (let n = this; n; n = n.parentNode || n.host) if (n instanceof Element) op *= Number(getComputedStyle(n).opacity);
  return op > 0.05; }`;

// no transitions while measuring (a state is read at once, not half-way): an inspector stylesheet for the page
// (job-hunt-agent's CSP blocks an added <style>), a <style> in each shadow root
async function noMotion(t) {
  await t.send("CSS.enable");
  const { frameTree } = await t.send("Page.getFrameTree");
  const { styleSheetId } = await t.send("CSS.createStyleSheet", { frameId: frameTree.frame.id });
  await t.send("CSS.setStyleSheetText", { styleSheetId, text: NO_MOTION });
  const { root } = await t.send("DOM.getDocument", { depth: -1, pierce: true });
  const shadows = [];
  const walk = (n) => { for (const s of n.shadowRoots || []) { shadows.push(s); walk(s); } for (const k of n.children || []) walk(k); if (n.contentDocument) walk(n.contentDocument); };
  walk(root);
  for (const s of shadows) {
    const { object } = await t.send("DOM.resolveNode", { nodeId: s.nodeId });
    await t.send("Runtime.callFunctionOn", { objectId: object.objectId, functionDeclaration: `function (css) { if (!this.querySelector("#jh-no-motion")) {
      const st = document.createElement("style"); st.id = "jh-no-motion"; st.textContent = css; this.append(st); } }`, arguments: [{ value: NO_MOTION }] });
  }
  return (await t.send("DOM.getDocument", { depth: -1, pierce: true })).root;
}

function controls(root, onlyPanel) {
  const out = [];
  if (onlyPanel) {                                       // the panel's own controls, not the page's
    const find = (n) => ((n.attributes || []).includes("data-jh-panel") ? n : [...(n.children || []), ...(n.shadowRoots || [])].map(find).find(Boolean));
    root = find(root);
    if (!root) return out;
  }
  const walk = (n) => {
    if (n.nodeType === 1 && out.length < MAX_NODES) {
      const a = n.attributes || [];
      const attr = (k) => { for (let i = 0; i < a.length; i += 2) if (a[i] === k) return a[i + 1]; return null; };
      const tag = n.nodeName.toLowerCase();
      const type = (attr("type") || "").toLowerCase();
      const control = tag === "button" || attr("role") === "button" || tag === "select" || tag === "textarea"
        || (tag === "input" && !["hidden", "checkbox", "radio", "range", "file", "color", "image"].includes(type));
      if (control) out.push({ nodeId: n.nodeId, tag, disabled: attr("disabled") !== null, canDisable: ["button", "input", "select", "textarea"].includes(tag) });
    }
    for (const s of n.shadowRoots || []) walk(s);
    for (const k of n.children || []) walk(k);
  };
  walk(root);
  return out;
}

async function call(t, nodeId, fn) {
  const { object } = await t.send("DOM.resolveNode", { nodeId });
  const r = await t.send("Runtime.callFunctionOn", { objectId: object.objectId, functionDeclaration: fn, returnByValue: true });
  return r.result.value;
}

// every visible control of the page as it is now, each look checked once in every state
async function audit(t, view, scheme, onlyPanel) {
  const root = await noMotion(t);
  const seen = new Set();
  let n = 0;
  for (const c of controls(root, onlyPanel)) {
    let normal;
    try {
      if (!(await call(t, c.nodeId, VISIBLE))) continue;
      normal = await call(t, c.nodeId, MEASURE);
    } catch { continue; }                               // redrawn meanwhile
    if (normal.empty || seen.has(normal.sig)) continue;
    seen.add(normal.sig);
    n++;
    for (const state of STATES) {
      if (state === "disabled" && !c.canDisable) continue;
      let got;
      try {
        if (state === "disabled") { if (!c.disabled) await t.send("DOM.setAttributeValue", { nodeId: c.nodeId, name: "disabled", value: "" }); }
        else if (state !== "normal") await t.send("CSS.forcePseudoState", { nodeId: c.nodeId, forcedPseudoClasses: state === "focus" ? ["focus", "focus-visible"] : [state] });
        got = state === "normal" ? normal : await call(t, c.nodeId, MEASURE);
      } catch { continue; } finally {
        if (state === "disabled" && !c.disabled) await t.send("DOM.removeAttribute", { nodeId: c.nodeId, name: "disabled" }).catch(() => {});
        else if (state !== "normal") await t.send("CSS.forcePseudoState", { nodeId: c.nodeId, forcedPseudoClasses: [] }).catch(() => {});
      }
      if (onlyPanel && got.filtered) failures.push({ view, scheme, state, what: got.what + " (the page put a filter on it)", fg: got.fg, bg: got.bg, ratio: 0, need: got.need });
      if (got.ratio < got.need) failures.push({ view, scheme, state, what: got.what, fg: got.fg, bg: got.bg, ratio: got.ratio, need: got.need });
      if (got.ph && got.ph.ratio < TEXT_MIN) failures.push({ view, scheme, state, what: got.what + " (placeholder)", fg: got.ph.fg, bg: got.bg, ratio: got.ph.ratio, need: TEXT_MIN });
    }
  }
  return n;
}

async function shot(t, name) {
  if (!SHOTS) return;
  for (const s of ["light", "dark"]) {
    await t.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: s }] });
    await sleep(300);
    writeFileSync(join(SHOTS, `${name}-${s}.png`), Buffer.from((await t.send("Page.captureScreenshot", { format: "png" })).data, "base64"));
  }
}
const scheme = (t, value) => t.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value }] });
async function both(t, view, onlyPanel = false) {
  let n = 0;
  for (const s of ["light", "dark"]) {
    await scheme(t, s);
    await sleep(250);
    n += await audit(t, view, s, onlyPanel);
  }
  check(`${view}: controls checked`, n > 0, `${n} looks`);
}

const b = await launch(CHROME, PROFILE_DIR);
try {
  // ---- the job-hunt-agent site --------------------------------------------------------------------------------------------
  const t = await tab(b);
  await t.send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:tester` });
  await t.until("document.querySelectorAll('#list li.row[id^=job-]').length >= 3", 20000);
  await sleep(1200);
  const tabs = await t.js(`return [...new Set([...document.querySelectorAll("#tabs [data-tab]")].map((x) => x.dataset.tab))]`);
  for (const name of tabs) {
    await t.js(`document.querySelector('#tabs [data-tab=${name}]').click(); return true`);
    await sleep(1500);
    await both(t, `tab ${name}`);
  }
  await t.js("document.querySelector('#tabs [data-tab=recommended]').click(); return true");
  await t.until("document.querySelectorAll('#list li.row[id^=job-]').length >= 3", 10000);
  await t.js("document.getElementById('job-0').click(); return true");
  await t.until("document.querySelector('#detail .acts')", 10000);
  await sleep(800);
  await both(t, "a job's panel");
  await t.js(`[...document.querySelectorAll("#detail .acts .lbl-btn")].find((x) => /Not interested/.test(x.textContent)).click(); return true`);
  check("Not interested shows its Why? box", await t.until("document.querySelector('#toasts .why .chip')", 8000));
  await both(t, "Not interested: the reasons");
  await t.js(`[...document.querySelectorAll("#toasts .why .chip")].find((x) => x.textContent.startsWith("Other")).click(); return true`);
  check("Other… gives its own reason box", await t.until("document.querySelector('#toasts .why input')", 4000));
  await both(t, "Not interested: the reason box (empty)");
  await t.js(`const i = document.querySelector("#toasts .why input"); i.value = "ZZ too far"; return true`);
  await both(t, "Not interested: the reason box (typed)");
  await shot(t, "not-interested-reason");
  await t.js("document.getElementById('settings-btn').click(); return true");
  await t.until("!!document.querySelector('.set-nav [data-sec]')", 8000);
  const secs = await t.js(`return [...document.querySelectorAll(".set-nav [data-sec]")].map((x) => x.dataset.sec)`);
  for (const sec of secs) {
    await t.js(`document.querySelector('.set-nav [data-sec=${sec}]').click(); return true`);
    await sleep(1200);
    await both(t, `Settings ${sec}`);
  }
  check("no page errors on the site", t.errors.length === 0, t.errors.join(" | "));

  // ---- the extension: options page, the panel on an Ashby page and on a page that tries to restyle it ---------------
  const { id } = await b.send("Extensions.loadUnpacked", { path: EXT_DIR });
  const o = await tab(b, `chrome-extension://${id}/options.html`);
  await o.until("document.getElementById('base')");
  const settings = async (base, token = TOKEN) => {   // a busy machine can see the page reload under the save: wait it out
    for (let i = 0; ; i++) {
      try { return await save(base, token); } catch (err) {
        if (i >= 2 || !/navigated or closed|context/i.test(String(err))) throw err;
        await o.until("document.getElementById('base')", 10000);
      }
    }
  };
  const save = (base, token) => o.js(`const $ = (i) => document.getElementById(i); $("base").value = ${JSON.stringify(base)}; $("token").value = ${JSON.stringify(token)};
    if (!document.querySelector("input[name=profile][value=tester]")) document.querySelector(".seg").append(Object.assign(document.createElement("input"), { type: "radio", name: "profile", value: "tester" }));
    document.querySelector("input[name=profile][value=tester]").checked = true; if ($("auto")) $("auto").checked = false;
    $("form").requestSubmit(); await new Promise((r) => setTimeout(r, 400)); return true`);
  await settings(BASE);
  await o.until("document.getElementById('base')", 10000);
  await both(o, "extension options page");

  const p = await tab(b);
  await serveFixtures(p, b, ["jobs.ashbyhq.com", "zz-hostile.example"], (u) => (u.hostname === "zz-hostile.example"
    ? { file: HOSTILE, type: "text/html" } : { file: join(FIX, "boards", "ashby.html"), type: "text/html" }));
  let ctx = null;
  const frame = (await p.send("Page.getFrameTree")).frameTree.frame.id;
  b.on((m) => { if (m.sessionId === p.sessionId && m.method === "Runtime.executionContextCreated" && m.params.context.name === "Job Hunt Agent Autofill"
    && m.params.context.auxData?.frameId === frame && m.params.context.auxData?.type === "isolated") ctx = m.params.context.id; });
  const ext = async (expr) => {
    for (let i = 0; i < 50 && !ctx; i++) await sleep(100);
    const r = await p.send("Runtime.evaluate", { expression: `(async () => { ${expr} })()`, contextId: ctx, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  };
  for (const [name, url] of [["the Ashby page", "https://jobs.ashbyhq.com/zz/1234/application"], ["a page that restyles everything", "https://zz-hostile.example/apply"]]) {
    await settings(BASE);
    ctx = null;
    await p.send("Page.navigate", { url });
    await p.until("document.readyState === 'complete'", 15000);
    await sleep(1500);
    await ext("if (!JH.panel.shown()) JH.top.message({ type: 'togglePanel' }); return true");
    await sleep(1200);
    await p.panelClick("Autofill");
    for (let i = 0; i < 60 && !/Filled \d|Needs you \(/.test(await p.panelText()); i++) await sleep(500);
    await both(p, `panel on ${name}`, true);
    await settings("http://127.0.0.1:9");                // job-hunt-agent not answering: the red box with its Settings button
    await p.panelClick("Autofill");
    check(`panel on ${name}: the error box shows`, await (async () => { for (let i = 0; i < 40; i++) { if ((await p.panelText()).includes("asleep or offline")) return true; await sleep(250); } return false; })(), await p.panelText());
    await both(p, `panel error box on ${name}`, true);
    await settings(BASE, "zz-wrong-token");             // job-hunt-agent refuses: the red box with its Settings button
    ctx = null;
    await p.send("Page.reload");
    await p.until("document.readyState === 'complete'", 15000);
    await sleep(1500);
    await ext("if (!JH.panel.shown()) JH.top.message({ type: 'togglePanel' }); return true");
    const hasSettings = async () => { for (let i = 0; i < 40; i++) { if (Boolean(await p.panelText()) && (await p.panelText()).includes("Settings")) return true; await sleep(250); } return false; };
    check(`panel on ${name}: the error box with its Settings button shows`, await hasSettings(), await p.panelText());
    await both(p, `panel Settings error box on ${name}`, true);
    if (SHOTS) {                                         // the Settings button in the red box, hovered
      const { root } = await p.send("DOM.getDocument", { depth: -1, pierce: true });
      const all = [];
      const walk = (n) => { all.push(n); for (const k of [...(n.children || []), ...(n.shadowRoots || [])]) walk(k); };
      walk(root);
      const btn = all.find((n) => n.nodeName === "BUTTON" && (n.attributes || []).includes("link") && (n.children || []).some((c) => c.nodeValue === "Settings"));
      if (btn) await p.send("CSS.forcePseudoState", { nodeId: btn.nodeId, forcedPseudoClasses: ["hover"] });
      await shot(p, `panel-error-${name.includes("Ashby") ? "ashby" : "hostile"}`);
    }
  }
} catch (err) {
  check("runner", false, err.stack || String(err));
} finally {
  b.close();
  console.log(JSON.stringify({ checks, failures }));
}

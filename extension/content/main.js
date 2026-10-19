// Job Hunt Agent Autofill, content script 6/n: the flow. Every frame can fill its own form ("fill" from the panel, through
// the service worker); the top frame runs the panel: which job, which resume, the address for the resume's city,
// Autofill, the counts and the Needs-you list. After the first Autofill in a tab, a new step or page is filled on
// its own when "Auto-fill each new page" is on (still never Next). A confirmation page after a Submit click the
// person made marks the job Applied in Job Hunt Agent, once.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  if (window.__jhMain) return;
  window.__jhMain = true;
  const SETTLE_MS = 700;
  const AUTO_MS = 1200;
  const SESSION_MAX_MS = 3 * 3600 * 1000;
  const SUBMIT_WINDOW_MS = 60 * 60 * 1000;
  const COLLECT_QUIET_MS = 2500;
  const COLLECT_MAX_MS = 120000;
  const ADDRESS_POLL_MS = 3000;
  const ADDRESS_POLLS = 30;
  const BOARD_MS = 150000;                          // one adapter step at most (Workday's My Experience is the long one)
  const GENERIC_MS = 60000;
  const FIELD_MS = 12000;
  const STAGE_MS = 900;
  const AUTO_MAX = 8;                                // auto-fills per page address and step (no loops)
  const KNOWN_FRAMES = /greenhouse\.io|lever\.co|ashbyhq\.com|icims\.com|smartrecruiters\.com|myworkdayjobs\.com|jobvite\.com|taleo\.net|workable\.com|bamboohr\.com|rippling\.com|breezy\.hr|applytojob\.com/i;
  const LEAVE_TEXT = /^(next( step)?|continue|save( (and|&) (continue|next))?|save|review|back|previous)$/i;
  const SUBMIT_TEXT = /^(submit( (my )?application)?|send( my)? application|apply|submit and finish|finish|complete( application)?)$/i;

  const board = () => JH.boards.find((b) => { try { return b.match(location.href, document); } catch { return false; } });
  const KIND = { combo: "select", listbox: "select" };

  function pageStyle() {
    if (document.getElementById("jh-style")) return;
    const st = document.createElement("style");
    st.id = "jh-style";
    st.textContent = "[data-jh-need]{outline:2px solid #f59e0b!important;outline-offset:2px!important}" +
      "[data-jh-hold]{outline:2px dashed #2f6fde!important;outline-offset:2px!important}" +
      "[data-jh-draft]{background:#e8f0fe!important;color:#111!important;outline:3px solid #1f5fbf!important;outline-offset:2px!important}" +
      "[data-jh-flash]{box-shadow:0 0 0 4px rgba(245,158,11,.55)!important;transition:box-shadow .3s}";
    (document.head || document.documentElement).append(st);
  }

  // ---- one frame's fill -----------------------------------------------------------------------------------------
  let resumeCache = null;
  async function resumeFile(ctx) {
    if (resumeCache) return resumeCache;
    const job = ctx.job || {};
    if (!job.uid && !job.app) { ctx.resumeError = "pick the job first, so the right resume is chosen"; return null; }
    const got = await JH.api("GET", "/api/autofill/resume", { query: { uid: job.uid, app: job.app }, binary: true });
    if (!got || !got.ok) { ctx.resumeError = (got && got.error) || "no resume"; return null; }
    resumeCache = JH.b64File(got.data.b64, got.data.name, got.data.type || "application/pdf");
    return resumeCache;
  }

  async function profileData(ctx) {
    const job = ctx.job || {};
    const got = await JH.api("GET", "/api/autofill/profile", { query: { uid: job.uid, app: job.app } });
    return got && got.ok ? got.data : null;
  }

  const need = (f, extra = {}) => ({ id: f.id, label: f.label, kind: f.kind, required: f.required,
    options: (extra.options || f.options || []).slice(0, 80), key: extra.key || null });

  // the words right around a control (its own small box), e.g. Ashby's "Autofill from resume" upload
  function nearText(el) {
    for (let node = el.parentElement, i = 0; node && i < 6; node = node.parentElement, i++) {
      const text = JH.text(node);
      if (text.length > 300) return "";
      if (text.length > 8) return text;
    }
    return "";
  }

  function resumeField(b, fields) {
    const files = fields.filter((f) => f.kind === "file" && !/cover|transcript|portfolio|other|additional|autofill|auto-fill/i.test(`${f.label} ${nearText(f.el)}`));
    const byLabel = files.find((f) => /resume|cv\b|curriculum/i.test(`${f.label} ${f.hints.join(" ")}`));
    const bySel = b.resume && files.find((f) => f.el.matches(b.resume));
    return byLabel || bySel || (files.length === 1 ? files[0] : null);
  }

  // a Skills box on any board gets the tailored resume's skills: a typeahead one skill at a time, a plain
  // box the list with commas
  const SKILLS = /^(key |technical |relevant |your )?skills\b|\bskills?( and| &) (tools|technologies)\b|^type to add skills/i;
  const CHIP = "[class*=chip], [class*=multi-value__label], [class*=multiValue] [class*=label], [class*=tag]:not(input), [class*=pill], [class*=token]";
  async function skillsInto(boxes, ctx, out) {
    const data = await ctx.profileData();
    const list = (data && data.skills) || [];
    if (!list.length) return;
    for (const f of boxes) {
      if (f.kind !== "combo") {
        if (!f.value) { JH.setValue(f.el, list.join(", ")); out.filled++; }
        continue;
      }
      let added = 0;
      const missing = [];
      const box = f.el.closest("[class*=field], [class*=Field], fieldset, .form-group") || f.el.parentElement;
      const chips = () => [...box.querySelectorAll(CHIP)].filter((c) => !c.closest("[role=listbox]")).map((c) => JH.pick.skillNorm(JH.text(c).replace(/[×✕x]$/, "")));
      for (const want of list.slice(0, 60)) {
        const before = chips();
        if (before.includes(JH.pick.skillNorm(want))) continue;                   // already in the box
        const got = await JH.fill.skill(f.el, want, { skip: (t) => before.includes(JH.pick.skillNorm(t)) });
        if (got.ok && !got.already) added++; else if (!got.ok) missing.push(want);
      }
      JH.mark(f.el);
      out.filled += added ? 1 : 0;
      if (missing.length) out.message = [out.message, `Skills not in the list: ${missing.join(", ")}`].filter(Boolean).join(" · ");
    }
  }

  async function generic(b, ctx, { skip } = {}) {
    if (JH.captcha() === "challenge") return { captcha: true, filled: 0, needs: [], skipped: 0 };
    const root = b.root ? b.root() : document;
    const collect = () => JH.fields.collect(root).filter((f) => !(skip && skip(f.el)));
    let fields = collect();
    const out = { filled: 0, needs: [], skipped: 0, kept: 0, held: 0 };
    const rf = resumeField(b, fields);
    if (rf && !rf.value && b.id !== "workday") {
      const file = await resumeFile(ctx);
      if (file && JH.setFile(rf.el, file)) {
        out.filled++;
        out.message = `Uploaded ${file.name}`;
        await JH.settle(500, (b.uploadWaitMs || 1500) + 2500);
        fields = collect();
      } else out.needs.push(need(rf, {}));
    }
    out.filled += fields.filter((f) => f.kind !== "file" && f.el.hasAttribute("data-jh-filled") && f.value).length;   // filled before
    let resumeParts = new Set();                         // user 2026-10-14: work history, education, summary on any site
    if (b.id !== "workday" && JH.experience.wanted(root)) {
      try {
        const xp = await JH.experience.fill(root, await ctx.profileData());
        out.filled += xp.filled;
        resumeParts = xp.handled;
        fields = collect();
      } catch (err) {
        JH.guard.report("error", `work history / education: ${String((err && err.message) || err).slice(0, 300)}`, b, stepOf(b));
      }
    }
    const skillBoxes = fields.filter((f) => SKILLS.test(f.label) && /^(combo|text|textarea)$/.test(f.kind) && !f.el.hasAttribute("data-jh-filled") && !f.hand);
    if (skillBoxes.length) await skillsInto(skillBoxes, ctx, out);
    const asked = fields.filter((f) => f.kind !== "file" && !f.el.hasAttribute("data-jh-filled") && !skillBoxes.includes(f) && !resumeParts.has(f.el));
    if (!asked.length) return out;
    const job = ctx.job || {};
    const res = await JH.api("POST", "/api/autofill/resolve", { body: {
      fields: asked.map(JH.fields.describe), board: b.id, company: job.company || "", section: b.section ? b.section() : "",
      uid: job.uid, app: job.app, source_url: (job.url || "") } });
    if (!res || !res.ok) { out.error = (res && res.error) || "Job Hunt Agent did not answer"; return out; }
    const byId = new Map(asked.map((f) => [f.id, f]));
    for (const ans of res.data.answers) {
      const f = byId.get(ans.id);
      if (!f) continue;
      f.el.removeAttribute("data-jh-need");
      f.el.removeAttribute("data-jh-hold");
      if (ans.status === "skip") { out.skipped++; continue; }
      if (ans.status === "hold") { out.held++; f.el.setAttribute("data-jh-hold", ""); continue; }
      if (ans.status === "needs") {
        const options = f.options && f.options.length ? f.options : await JH.fill.readOptions(f).catch(() => []);
        out.needs.push(need(f, { ...ans, options }));
        continue;
      }
      if (!JH.guard.attempt(f.id)) { out.skipped++; continue; }             // tried enough on this page: no loop
      let got = null;
      try {
        const overwrite = typeof b.overwrite === "function" ? b.overwrite(f, ans) : Boolean(b.overwrite);
        got = await JH.guard.run(async () => (b.fillField && (await b.fillField(f, ans))) || JH.fill.field(f, ans, { overwrite }), FIELD_MS, "a field");
      } catch (err) {
        got = { status: "needs", why: String(err && err.message) };
      }
      if (got.status === "filled") out.filled++;
      else if (got.status === "kept") out.kept++;
      else if (got.status === "skipped") out.skipped++;
      else out.needs.push(need(f, { options: got.options, key: ans.key }));
    }
    return out;
  }

  function highlight(needs) {
    for (const n of needs) {
      const el = document.querySelector(`[data-jh-id="${n.id}"]`);
      const box = el && el.closest("[data-automation-id^=formField-], .field, .form-group, fieldset");
      const controls = box ? [...box.querySelectorAll("input:not([type=hidden]), select, textarea, button[aria-haspopup]")] : [];
      const own = controls.every((c) => c === el || (el.name && c.name === el.name) || c.closest("[data-automation-id=dateInputWrapper]"));
      const target = el && (box && own ? box : el);      // the question's own box, never a box holding other questions
      if (target) target.setAttribute("data-jh-need", "");
    }
  }

  const fieldsNow = (b) => { try { return JH.fields.collect(b.root ? b.root() : document); } catch { return []; } };
  const stepOf = (b) => { try { return b.stepLabel ? b.stepLabel() : ""; } catch { return ""; } };
  const kindOf = (b) => { try { return b.step ? b.step() : ""; } catch { return ""; } };

  // the board's own step with a time limit; an error, a timeout or a layout it does not know is reported
  // (with the page's form) and the plain filler takes over, so the page always gets what can be filled
  async function guardedFill(b, full) {
    const generic = b.id === "generic";
    try {
      if (!generic && b.unknown && b.unknown()) {
        JH.guard.report("unknown_layout", `${b.name}: a step this adapter does not know`, b, stepOf(b));
        return await JH.guard.run(() => full.generic({}), GENERIC_MS, "the plain filler");
      }
      return await JH.guard.run(() => (b.fill ? b.fill(full) : full.generic({})), generic ? GENERIC_MS : BOARD_MS, `${b.name}'s step`);
    } catch (err) {
      JH.guard.report(err && err.timeout ? "timeout" : "error", String((err && err.stack) || err).slice(0, 500), b, stepOf(b));
      if (generic) return { filled: 0, needs: [], skipped: 0, error: "Autofill hit a problem on this page (reported to Job Hunt Agent)" };
      const rest = await JH.guard.run(() => full.generic({}), GENERIC_MS, "the plain filler").catch(() => ({ filled: 0, needs: [], skipped: 0 }));
      return { ...rest, message: "This page tripped Autofill: it filled what it could the plain way (reported to Job Hunt Agent)" };
    }
  }

  // every field of this step for the panel's list (Needs you, Filled, Yours, Skipped)
  function rowsNow(b) {
    return fieldsNow(b).filter((f) => f.label || f.kind !== "file").slice(0, 150).map((f) => ({ id: f.id, label: f.label, kind: f.kind,
      value: f.kind === "checkbox" ? (f.value ? "Yes" : "") : String(f.value || "").slice(0, 300), status: JH.learn.status(f),
      options: (f.options || []).slice(0, 60) }));
  }

  // this step's hand answers go to the service worker as the user works; Job Hunt Agent gets them when the user leaves the step
  let stageTimer = null;
  let filledHere = false;                              // Autofill ran on this page (the user asked it to fill this form)
  // only job applications teach it: a page with its job known, a job board's own form, or a form the user had
  // Autofill fill. Never Job Hunt Agent's own pages, a site search box, a shop's checkout or a newsletter sign-up.
  async function applicationPage(b, job) {
    const { base } = await JH.store(["base"]);
    try { if (base && new URL(base).origin === location.origin) return false; } catch { /* a bad saved address: not Job Hunt Agent */ }
    return Boolean(job) || b.id !== "generic" || filledHere;
  }
  async function stageNow() {
    const b = board();
    const got0 = await JH.send({ type: "session:get" });   // the tab's job, once this page's own job is known (no Autofill click needed)
    const s = got0 && got0.ok ? got0.data : null;
    const job = s && s.job && s.jobHost && s.jobHost === hostOf(s.tabUrl) ? s.job : null;
    if (!(await applicationPage(b, job))) return;
    const { account } = await JH.store(["account"]);
    const fields = fieldsNow(b);
    const got = JH.learn.batch(fields, b, (account && account.password) || "");
    if (JH.panel && (JH.isTop || fields.length)) JH.send({ type: "toTop", message: { type: "rows", rows: rowsNow(b) } });
    if (!got.items.length && !got.hand) return;            // without a job: global answers only (no company to keep one for)
    await JH.send({ type: "learn:stage", key: `${location.href.split("#")[0]}|${stepOf(b)}`, batch: { ...got, uid: job && job.uid,
      app: job && job.app, board: b.id, company: (job && job.company) || "", step: stepOf(b), stage: kindOf(b) } });
  }
  const stageSoon = () => { clearTimeout(stageTimer); stageTimer = setTimeout(stageNow, STAGE_MS); };
  async function leaveStep() { clearTimeout(stageTimer); await stageNow(); JH.send({ type: "learn:flush" }); }
  for (const type of ["change", "input"]) document.addEventListener(type, (e) => { if (JH.realEvent(e)) stageSoon(); }, true);
  document.addEventListener("click", (e) => { if (e.isTrusted && !(e.target instanceof Element && e.target.closest("[data-jh-panel]"))) stageSoon(); }, true);
  addEventListener("pagehide", () => { stageNow(); JH.send({ type: "learn:flush" }); });

  // the panel's edit: fill the page field with what the user chose (the user's answer: it is learned like a hand fill)
  async function setField(id, value) {
    const b = board();
    const f = fieldsNow(b).find((x) => x.id === id);
    if (!f) return;
    JH.learn.panelSet(id);
    try {
      await JH.guard.run(() => JH.fill.field({ ...f, hand: false }, { value }, { overwrite: true, force: true }), FIELD_MS, "a field");
    } catch (err) {
      JH.send({ type: "toTop", message: { type: "note", text: `Could not fill it: ${err.message}` } });
    }
    f.el.closest("[data-jh-need]")?.removeAttribute("data-jh-need");
    stageSoon();
  }

  let filling = false;
  async function fillFrame(ctx) {
    if (filling) return;
    filling = true;
    try {
      pageStyle();
      const b = board();
      if (!JH.isTop && !JH.fields.collect(b.root ? b.root() : document).length) return;   // a frame with no form: quiet
      if (JH.captcha() === "challenge") {                 // never solved or worked around: the user solves it, then Autofill
        JH.send({ type: "toTop", message: { type: "result", result: { board: b.name, filled: 0, skipped: 0, held: 0, captcha: true, message: "", needs: [] } } });
        return;
      }
      const full = { ...ctx };
      full.resumeFile = () => resumeFile(full);
      full.profileData = () => profileData(full);
      full.generic = (opts) => generic(b, full, opts);
      JH.learn.see(fieldsNow(b));
      filledHere = true;
      const result = await guardedFill(b, full);
      result.needs = result.needs || [];
      if (JH.captcha() === "box") result.message = [result.message, "Tick “I'm not a robot” yourself"].filter(Boolean).join(" · ");
      highlight(result.needs);
      const job = ctx.job || {};
      if (result.filled || result.needs.length) {
        await JH.api("POST", "/api/autofill/report", { body: { uid: job.uid, app: job.app, board: b.id, company: job.company,
          step: b.stepLabel ? b.stepLabel() : "", filled: result.filled, skipped: result.skipped || 0,
          needs: result.needs.filter((n) => n.kind !== "file").map((n) => ({ label: n.label, kind: KIND[n.kind] || n.kind,
            options: n.options, key: n.key })) } });
      }
      JH.learn.filled(fieldsNow(b));
      JH.send({ type: "toTop", message: { type: "result", result: { board: b.name, filled: result.filled || 0, skipped: result.skipped || 0,
        held: result.held || 0, captcha: Boolean(result.captcha), message: result.message || result.error || "",
        needs: result.needs.map((n) => ({ id: n.id, label: n.label, kind: n.kind })), rows: rowsNow(b) } } });
    } finally {
      filling = false;
    }
  }

  async function snapshot() {
    const b = board();
    if (!JH.fields.collect(b.root ? b.root() : document).length) return;
    const html = await JH.guard.pageHtml();           // no password, no ID / bank / birth-date values
    await JH.api("POST", "/api/autofill/snapshot", { body: { url: location.href, board: b.id, step: stepOf(b), html } });
    JH.send({ type: "toTop", message: { type: "note", text: "Page snapshot sent to Job Hunt Agent" } });
  }

  function focusField(id) {
    const el = document.querySelector(`[data-jh-id="${id}"]`);
    if (!el) return;
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    el.focus({ preventScroll: true });
    const box = el.closest("[data-automation-id^=formField-], .field, .form-group, fieldset") || el;
    box.setAttribute("data-jh-flash", "");
    setTimeout(() => box.removeAttribute("data-jh-flash"), 1600);
  }

  function setDraft(id, text) {
    const el = document.querySelector(`[data-jh-id="${id}"]`);
    if (!el) return;
    JH.setValue(el, text);
    el.setAttribute("data-jh-draft", "");
    el.closest("[data-jh-need]")?.removeAttribute("data-jh-need");
  }

  // ---- auto-fill each new step, and the confirmation page -----------------------------------------------------
  const seen = new Set();
  const autoRuns = new Map();
  const signature = () => { const b = board(); return `${location.href}|${b.signature ? b.signature() : JH.fields.collect(b.root ? b.root() : document).length}`; };

  async function session() {
    const got = await JH.send({ type: "session:get" });
    const s = got && got.ok ? got.data : null;
    return s && s.started && Date.now() - s.at < SESSION_MAX_MS ? s : null;
  }

  const hostOf = (url) => { try { return new URL(url).host; } catch { return ""; } };

  async function watchStep() {
    const s = await session();
    if (!s) return;
    const b = board();
    if (!s.applied && s.submitted && Date.now() - s.submitted < SUBMIT_WINDOW_MS && b.confirmed && b.confirmed()) {
      JH.send({ type: "toTop", message: { type: "confirmed" } });
      return;
    }
    const { auto } = await JH.store(["auto"]);
    if (auto === false || s.auto === false) return;
    if (!s.jobHost || s.jobHost !== hostOf(s.tabUrl)) return;   // this page's own job is not known yet (or another site)
    const sig = signature();
    if (seen.has(sig)) return;
    const fields = JH.fields.collect(b.root ? b.root() : document);
    if (!fields.length && !(b.step && ["auth", "verify", "review"].includes(b.step()))) return;
    if (seen.size) JH.send({ type: "learn:flush" });     // a new step: the last one's hand answers go to Job Hunt Agent
    seen.add(sig);
    const runKey = `${location.href}|${stepOf(b)}`;
    const n = (autoRuns.get(runKey) || 0) + 1;
    autoRuns.set(runKey, n);
    if (n > AUTO_MAX) return;                           // never an auto-fill loop on one page
    await fillFrame({ job: s.job || null, auto: true });
  }

  let stepTimer = null;
  new MutationObserver(() => { clearTimeout(stepTimer); stepTimer = setTimeout(watchStep, AUTO_MS); })
    .observe(document.documentElement, { childList: true, subtree: true });
  setTimeout(watchStep, AUTO_MS);

  document.addEventListener("click", (e) => {
    if (!e.isTrusted) return;
    const btn = e.target instanceof Element && e.target.closest("button, input[type=submit], [role=button]");
    if (!btn || btn.closest("[data-jh-panel]")) return;
    const text = JH.text(btn) || btn.value || "";
    if (SUBMIT_TEXT.test(text.trim()) || /submit/i.test(btn.getAttribute("data-automation-id") || "")) {
      JH.send({ type: "session:set", patch: { submitted: Date.now() } });
    }
    if (SUBMIT_TEXT.test(text.trim()) || LEAVE_TEXT.test(text.trim()) || /next|submit/i.test(btn.getAttribute("data-automation-id") || "")) leaveStep();
  }, true);

  // ---- messages from the panel (through the service worker) ---------------------------------------------------
  chrome.runtime.onMessage.addListener((msg) => {
    if (!msg) return;
    if (msg.type === "fill") {
      seen.add(signature());
      fillFrame(msg.ctx || {});
    } else if (msg.type === "recheck") watchStep();
    else if (msg.type === "snapshot") snapshot();
    else if (msg.type === "setDraft") setDraft(msg.id, msg.text);
    else if (msg.type === "focus") focusField(msg.id);
    else if (msg.type === "setField") setField(msg.id, msg.value);
    else if (msg.type === "rowsPlease") stageNow();
    else if (JH.isTop) JH.top.message(msg);
  });

  // ---- the top frame: the panel ---------------------------------------------------------------------------------
  if (!JH.isTop) return;
  const results = new Map();
  let collectTimer = null, collectCap = null, job = null, pollN = 0, dropped = false;
  const TAB_JOB_MS = 30 * 60 * 1000;
  const FRAME_WAIT_MS = 30000;
  let fillStarted = 0;
  const CAREERS = /job|career|apply|talent|recruit|hiring|opening|position|vacanc/i;
  const P = (patch) => JH.panel.set(patch);
  const jobQuery = () => ({ uid: job && job.uid, app: job && job.app });

  function appLike() {
    const b = board();
    if (b.id !== "generic") return true;
    if ([...document.querySelectorAll("iframe")].some((f) => KNOWN_FRAMES.test(f.src || ""))) return true;
    return Boolean(document.querySelector("input[type=file]") && document.querySelector("input[type=email], input[name*=email i], input[autocomplete=email]"));
  }

  async function refresh() {
    const frames = [...document.querySelectorAll("iframe")].map((f) => f.src).filter((s) => KNOWN_FRAMES.test(s || "")).slice(0, 6);
    const got = await JH.api("GET", "/api/autofill/match", { query: { url: location.href, frames: JSON.stringify(frames) } });
    if (!got || !got.ok) return P({ loaded: true, error: (got && got.error) || "Job Hunt Agent did not answer" });
    job = got.data.job;
    if (!job && !dropped) {                               // an apply page reached from the posting matched in this tab
      const got2 = await JH.send({ type: "session:get" });
      const s = got2 && got2.ok ? got2.data : null;
      if (s && s.job && Date.now() - (s.jobAt || 0) < TAB_JOB_MS) return pick({ uid: s.job.uid, app: s.job.app }, false);
    }
    P({ loaded: true, error: null, job, recent: got.data.recent || [], resume: got.data.resume, address: got.data.address,
        sponsor: got.data.sponsor || null, openNeeds: got.data.needs });
    JH.send({ type: "broadcast", message: { type: "rowsPlease" } });
    if (job) await ownJob(job);
    pollAddress();
  }

  // this page's job is known: the tab remembers it for this site, and auto-fill may start
  async function ownJob(j) {
    await JH.send({ type: "session:set", patch: { job: j, jobAt: Date.now(), jobHost: location.host } });
    JH.send({ type: "broadcast", message: { type: "recheck" } });
  }

  async function pollAddress() {
    const a = JH.panel.get().address;
    if (!a || a.kind !== "looking" || pollN >= ADDRESS_POLLS) return;
    pollN++;
    await JH.sleep(ADDRESS_POLL_MS);
    const got = await JH.api("GET", "/api/autofill/address", { query: jobQuery() });
    if (got && got.ok) P({ address: got.data });
    pollAddress();
  }

  // done when every embedded form (a Greenhouse / iCIMS frame) has answered, or it has taken too long
  const formFrameCount = () => [...document.querySelectorAll("iframe")].filter((f) => KNOWN_FRAMES.test(f.src || "")).length;
  function quietDone() {
    const frames = [...results.keys()].filter((k) => k !== location.href).length;
    if (frames < formFrameCount() && Date.now() - fillStarted < FRAME_WAIT_MS) { collectTimer = setTimeout(quietDone, COLLECT_QUIET_MS); return; }
    finishCollect();
  }

  const frameRows = new Map();                         // frame -> its fields, for the panel's list
  const allRows = () => [...frameRows.values()].flat();
  function finishCollect() {
    clearTimeout(collectTimer);
    clearTimeout(collectCap);
    const all = [...results.values()];
    const sum = (k) => all.reduce((n, r) => n + (r[k] || 0), 0);
    const needs = all.flatMap((r) => r.needs || []);
    const messages = [...new Set(all.map((r) => r.message).filter(Boolean))];
    P({ busy: false, captcha: all.some((r) => r.captcha), message: messages.join(" · ") || null,
        counts: { filled: sum("filled"), needs: needs.length + sum("held"), skipped: sum("skipped"),
                  notes: sum("held") ? `${sum("held")} address field(s) wait for the address above` : null },
        needs, rows: allRows() });
    JH.api("GET", "/api/autofill/count").then((r) => { if (r && r.ok) P({ openNeeds: r.data.open }); });
  }

  // Job Hunt Agent must be up before filling (the Mac may be asleep or offline); says which version is current
  const OWN_VERSION = chrome.runtime.getManifest().version;
  const newer = (a, b) => { const x = String(a).split(".").map(Number), y = String(b).split(".").map(Number); for (let i = 0; i < 3; i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0); } return false; };
  async function healthy() {
    const got = await JH.api("GET", "/api/autofill/health", { query: { v: OWN_VERSION } });
    if (got && got.ok) {
      const latest = got.data.extension;
      const how = "download it again from Job Hunt Agent › Settings › Autofill extension, replace the folder, then ↻ on its card in chrome://extensions (a folder loaded from your clone: pull, then ↻)";
      P({ health: { ok: true }, update: latest && newer(latest, OWN_VERSION) ? `Update ${latest} is ready: ${how}` : null });
      return true;
    }
    const asleep = !got || !got.status;
    P({ health: { ok: false, message: asleep ? "Is the computer running Job Hunt Agent asleep or offline? Wake it (or start job-hunt-agent serve), then click Autofill again."
      : `Job Hunt Agent answered: ${got.error}` } });
    return false;
  }

  async function autofill() {
    if (JH.panel.get().busy) return;
    if (!(await healthy())) return;
    results.clear();
    const { auto } = await JH.store(["auto"]);
    fillStarted = Date.now();
    P({ busy: true, message: null, counts: null, needs: [], applied: JH.panel.get().applied });
    await JH.send({ type: "session:set", patch: { job, started: Date.now(), auto: auto !== false, jobHost: location.host } });
    JH.send({ type: "broadcast", message: { type: "fill", ctx: { job } } });
    collectCap = setTimeout(finishCollect, COLLECT_MAX_MS);
  }

  async function markApplied(auto) {
    if (!job) return P({ message: "Pick the job first, then Mark applied" });
    const got = await JH.api("POST", "/api/autofill/applied", { body: jobQuery() });
    if (!got || !got.ok) return P({ message: `Not marked applied: ${(got && got.error) || "Job Hunt Agent did not answer"}` });
    await JH.send({ type: "session:set", patch: { applied: Date.now() } });
    const where = got.data.address ? ` · Address: ${got.data.address}` : "";
    P({ applied: got.data.already ? "Already marked applied in Job Hunt Agent" : `${auto ? "Confirmation seen: " : ""}marked Applied in Job Hunt Agent ✓${where}` });
  }

  JH.top = {
    autofill,                                            // the panel's Autofill (the nightly canary calls it when a page covers the button)
    message(msg) {
      if (msg.type === "togglePanel") {
        const first = !JH.panel.shown() && !JH.panel.get().loaded;
        JH.panel.toggle();
        if (first) refresh();
      } else if (msg.type === "result") {
        results.set(msg.frameUrl || "", msg.result);       // a frame that fills again (a new step) replaces its count
        if (msg.result.rows) frameRows.set(msg.frameUrl || "", msg.result.rows);
        if (!JH.panel.shown()) { JH.panel.show(); if (!JH.panel.get().loaded) refresh(); }
        clearTimeout(collectTimer);
        collectTimer = setTimeout(quietDone, COLLECT_QUIET_MS);
        const formFrames = [...document.querySelectorAll("iframe")].some((f) => KNOWN_FRAMES.test(f.src || ""));
        if (!JH.panel.get().busy || (msg.frameUrl === location.href && !formFrames)) finishCollect();   // no other form will answer
      } else if (msg.type === "rows") {
        frameRows.set(msg.frameUrl || "", msg.rows || []);
        if (!JH.panel.get().busy) P({ rows: allRows() });
      } else if (msg.type === "learned") {
        P({ learned: [...(JH.panel.get().learned || []), ...(msg.entries || [])].slice(-12) });
      } else if (msg.type === "confirmed") {
        session().then((s) => { if (s && !s.applied) markApplied(true); });
      } else if (msg.type === "note") P({ message: msg.text });
    },
  };

  async function pick(r, remember = true) {
    const got = remember ? await JH.api("POST", "/api/autofill/remember", { body: { url: location.href, uid: r.uid, app: r.app } })
      : await JH.api("GET", "/api/autofill/match", { query: { url: location.href, uid: r.uid, app: r.app } });
    if (!got || !got.ok) return P({ loaded: true, message: (got && got.error) || "Could not save the pick" });
    resumeCache = null;
    if (remember) return refresh();
    job = got.data.job;
    P({ loaded: true, error: null, job: job && { ...job, how: "tab" }, resume: got.data.resume, address: got.data.address,
        sponsor: got.data.sponsor || null, openNeeds: got.data.needs });
    pollAddress();                                       // shown only: it fills when the user clicks Autofill on this page
  }

  JH.panel.on({
    autofill,
    notThis: async () => {                                  // "Not this job": the picker, and this tab forgets the job
      dropped = true;
      job = null;
      await JH.send({ type: "session:set", patch: { job: null, jobHost: null } });
      const got = await JH.api("GET", "/api/autofill/match", { query: { url: location.href, recent: 1 } });
      P({ job: null, resume: null, address: null, recent: (got && got.ok && got.data.recent) || [] });
    },
    auto: (on) => { chrome.storage.local.set({ auto: on }); JH.send({ type: "session:set", patch: { auto: on } }); P({ auto: on }); },
    pick: (r) => { dropped = false; return pick(r, true); },
    async address(action, data) {
      const got = await JH.api("POST", `/api/autofill/address/${action}`, { body: { ...jobQuery(), ...data } });   // the job's tracker row too
      if (!got || !got.ok) return P({ message: (got && got.error) || "Could not save the address" });
      pollN = 0;
      if (got.data.kind) P({ address: got.data });
      else await refresh();
      if (action === "use") P({ message: "Address saved for this city and this job's tracker row" });
      pollAddress();
    },
    applied: () => markApplied(false),
    snapshot: () => JH.send({ type: "broadcast", message: { type: "snapshot" } }),
    async draft(n) {
      P({ message: "Drafting with Claude…" });
      const got = await JH.api("POST", "/api/autofill/draft", { body: { ...jobQuery(), question: n.label } });
      if (!got || !got.ok) return P({ message: (got && got.error) || "Draft failed" });
      JH.send({ type: "broadcast", message: { type: "setDraft", id: n.id, text: got.data.text } });
      P({ message: "Draft put in the box (shaded blue, solid outline): read it before you go on", needs: JH.panel.get().needs.filter((x) => x.id !== n.id) });
    },
    focus: (n) => JH.send({ type: "broadcast", message: { type: "focus", id: n.id } }),
    settings: () => JH.send({ type: "openOptions" }),
    setField: (r, value) => JH.send({ type: "broadcast", message: { type: "setField", id: r.id, value } }),
    async snippets(r) {
      const got = await JH.api("GET", "/api/autofill/snippets", { query: { q: r.label } });
      return (got && got.ok && got.data.snippets) || [];
    },
    async undo(x) {
      const got = await JH.api("POST", "/api/autofill/learn/undo", { body: { id: x.id } });
      if (!got || !got.ok) return P({ message: `Not undone: ${(got && got.error) || "Job Hunt Agent did not answer"}` });
      P({ learned: (JH.panel.get().learned || []).map((y) => (y.id === x.id ? { ...y, undone: true } : y)) });
    },
    viewResume: () => job && JH.send({ type: "viewResume", uid: job.uid || "", app: job.app || "" }),
  });

  (async () => {
    const s = await JH.store(["profile", "auto"]);
    const name = s.profile || "your profile";
    P({ profileName: name[0].toUpperCase() + name.slice(1), auto: s.auto !== false, version: OWN_VERSION });
    const last = await JH.send({ type: "learn:last" });  // "Saved for next time" from the step the user just left
    if (last && last.ok && last.data && last.data.length) P({ learned: last.data });
    await JH.send({ type: "session:set", patch: { jobHost: null } });   // a new page: no auto-fill until its job is known
    const active = await session();
    await JH.settle(600, 4000);                           // after the page's own first render (hydration): no clash
    if (appLike() || active) {
      JH.panel.show();
      healthy();
      await refresh();
    } else if (CAREERS.test(location.hostname + location.pathname)) {   // a posting page: matched quietly, so the
      const got = await JH.api("GET", "/api/autofill/match", { query: { url: location.href } });   // apply page after it knows the job
      if (got && got.ok && got.data.job) JH.send({ type: "session:set", patch: { job: got.data.job, jobAt: Date.now() } });
    }
  })();
})();

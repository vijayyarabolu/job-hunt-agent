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
  const COLLECT_MAX_MS = 180000;
  const ADDRESS_POLL_MS = 3000;
  const ADDRESS_POLLS = 30;
  const BOARD_MS = 150000;                          // one adapter step at most (Workday's My Experience is the long one)
  const GENERIC_MS = 120000;                       // a long form + one resume-answers call
  const FIELD_MS = 12000;
  const STAGE_MS = 900;
  const AUTO_MAX = 8;                                // auto-fills per page address and step (no loops)
  const KNOWN_FRAMES = /greenhouse\.io|lever\.co|ashbyhq\.com|icims\.com|smartrecruiters\.com|myworkdayjobs\.com|jobvite\.com|taleo\.net|workable\.com|bamboohr\.com|rippling\.com|breezy\.hr|applytojob\.com|dover\.com|paylocity\.com|successfactors\.com|oraclecloud\.com|adp\.com/i;
  const LEAVE_TEXT = /^(next( step)?|continue|save( (and|&) (continue|next))?|save|review|back|previous)$/i;
  const SUBMIT_TEXT = /^(submit( (my )?application)?|send( my)? application|apply|submit and finish|finish|complete( application)?)$/i;

  const board = () => JH.boards.find((b) => { try { return b.match(location.href, document); } catch { return false; } });
  const KIND = { combo: "select", listbox: "select" };
  const PLACE_KEYS = new Set(["location", "city", "state", "zip", "address1"]);   // GOAL30 §3: the fields a Location choice moves

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

  // GOAL27 "profile first, once": the structured profile for this job, kept for the page (every frame asks once)
  let profileCache = null;
  async function profileData(ctx) {
    const job = ctx.job || {};
    const key = `${job.uid || ""}|${job.app || ""}`;
    if (profileCache && profileCache.key === key) return profileCache.data;
    const got = await JH.api("GET", "/api/autofill/profile", { query: { uid: job.uid, app: job.app } });
    const data = got && got.ok ? got.data : null;
    if (data) profileCache = { key, data };
    return data;
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
      options: (f.options || []).slice(0, 60), key: (f.cls && !f.cls.group && f.cls.key) || null, group: (f.cls && f.cls.group) || null,
      hints: (f.hints || []).slice(0, 6), required: Boolean(f.required), origin: f.origin || null,
      max: f.el && f.el.maxLength > 0 ? f.el.maxLength : null }));             // GOAL29 §5: an open question's limit
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

  // the panel's edit: fill the page field with what the user chose (the user's answer: it is learned like a hand fill),
  // through the board's own widgets (Workday's date parts and prompts too); afterwards the field is the user's, so the
  // next Autofill never puts the old answer back
  async function setField(id, value, { explicit = false, place = null } = {}) {
    const b = board();
    const f = fieldsNow(b).find((x) => x.id === id);
    if (!f) return;
    if (explicit) JH.learn.explicit(id); else JH.learn.panelSet(id);
    const ans = { value, option: "", force: true, ...(place ? { place } : {}) };   // the user's own edit: over what the user typed too
    let got = null;
    try {
      got = await JH.guard.run(async () => (b.fillField && (await b.fillField({ ...f, hand: false }, ans))) ||
        JH.fill.field({ ...f, hand: false }, ans, { overwrite: true, force: true }), FIELD_MS, "a field");
    } catch (err) {
      JH.send({ type: "toTop", message: { type: "note", text: `Could not fill it: ${err.message}` } });
    }
    if (got && got.status === "filled") {
      for (const el of [f.el, ...(f.els || [])]) JH.markHand(el);
      if (f.wdDate) f.wdDate.querySelectorAll("input").forEach((i) => JH.markHand(i));
      JH.marks.set(f.el, "filled", "", null);
    } else if (got && got.status !== "kept") {
      JH.send({ type: "toTop", message: { type: "note", text: `Not filled: ${got.why || "the page did not take it"}` } });
    }
    f.el.closest("[data-jh-need]")?.removeAttribute("data-jh-need");
    stageSoon();
  }

  let filling = false, again = null;
  async function fillFrame(ctx) {
    // the user's click, or a new step's auto-fill (the user pressed Save and Continue), while a fill runs: one more pass after it
    if (filling) { if (!ctx.auto || !again) again = ctx; return; }
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
      full.generic = (opts) => JH.engine.run(b, full, opts);
      JH.learn.see(fieldsNow(b));
      const step = b.stepLabel ? b.stepLabel() : "";     // the step it fills: the page may be on the next one when it reports
      filledHere = true;
      const result = await guardedFill(b, full);
      result.needs = result.needs || [];
      if (JH.captcha() === "box") result.message = [result.message, "Tick “I'm not a robot” yourself"].filter(Boolean).join(" · ");
      highlight(result.needs);
      const job = ctx.job || {};
      if (result.filled || result.needs.length) {
        await JH.api("POST", "/api/autofill/report", { body: { uid: job.uid, app: job.app, board: b.id, company: job.company,
          step, filled: result.filled, skipped: result.skipped || 0,
          needs: result.needs.filter((n) => n.kind !== "file" && n.why !== "sensitive: yours to type").map((n) => ({ label: n.label, kind: KIND[n.kind] || n.kind,
            options: n.options, key: n.key })),
          fields: (result.fields || []).slice(0, 150), version: JH.version } });   // GOAL27: per-field log
      }
      JH.learn.filled(fieldsNow(b));
      if (again) return;                                    // the user's Autofill click is queued: that pass reports
      JH.send({ type: "toTop", message: { type: "result", result: { board: b.name, filled: result.filled || 0, skipped: result.skipped || 0,
        held: result.held || 0, review: result.review || 0, captcha: Boolean(result.captcha), message: result.message || result.error || "",
        needs: result.needs.map((n) => ({ id: n.id, label: n.label, kind: n.kind })), rows: rowsNow(b) } } });
    } finally {
      filling = false;
      if (again) { const next = again; again = null; fillFrame(next); }
    }
  }

  // GOAL31: the top frame always sends (a page where no field is found is the one to fix), and the panel says what
  // Job Hunt Agent answered, never "sent" for a page it refused
  async function snapshot() {
    const b = board();
    if (!JH.isTop && !JH.fields.collect(b.root ? b.root() : document).length) return;   // a frame with no form: quiet
    const html = await JH.guard.pageHtml();           // no password, no ID / bank / birth-date values
    const got = await JH.api("POST", "/api/autofill/snapshot", { body: { url: location.href, board: b.id, step: stepOf(b), html } });
    const text = got && got.ok ? "Page snapshot kept in Job Hunt Agent" : `Page snapshot not kept: ${(got && got.error) || "Job Hunt Agent did not answer"}`;
    JH.send({ type: "toTop", message: { type: "note", text } });
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

  // GOAL27 §5: the cover letter the user asked for, pasted into this frame's cover letter box or uploaded as its PDF. A
  // frame without one stays quiet; the top frame says so when no frame answers (GOAL34 §2: never a silent button)
  const coverNote = (text) => JH.send({ type: "toTop", message: { type: "note", text, cover: true } });
  async function coverInto(msg) {
    const fields = fieldsNow(board());
    const isCover = (f) => f.cls && (f.cls.key === "cover_letter" || f.cls.key === "cover_letter_file");
    if (msg.mode === "text") {
      const f = fields.find((x) => isCover(x) && /^(textarea|text)$/.test(x.kind));
      if (!f) return;
      const ok = await JH.fill.putText(f.el, msg.text);
      JH.mark(f.el);
      JH.marks.set(f.el, ok ? "review" : "failed", "your cover letter: read it");
      coverNote(ok ? "Cover letter pasted (marked ? review)" : "The cover letter box did not keep the text");
      return;
    }
    const f = fields.find((x) => x.kind === "file" && isCover(x));
    if (!f) return;
    const got = await JH.api("GET", "/api/autofill/cover.pdf", { query: { uid: msg.job && msg.job.uid, app: msg.job && msg.job.app }, binary: true });
    if (!got || !got.ok) return coverNote(`No cover letter PDF: ${(got && got.error) || "Job Hunt Agent did not answer"}`);
    const ok = JH.setFile(f.el, JH.b64File(got.data.b64, got.data.name, "application/pdf"));
    JH.marks.set(f.el, ok ? "filled" : "failed");
    coverNote(ok ? `Uploaded ${got.data.name}` : "The upload box did not take the PDF: use Download PDF and drop it in");
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
    else if (msg.type === "setField") setField(msg.id, msg.value, { explicit: msg.explicit, place: msg.place });
    else if (msg.type === "rowsPlease") stageNow();
    else if (msg.type === "coverInto") coverInto(msg);
    else if (JH.isTop) JH.top.message(msg);
  });

  // ---- the top frame: the panel ---------------------------------------------------------------------------------
  if (!JH.isTop) return;
  const results = new Map();
  let collectTimer = null, collectCap = null, job = null, pollN = 0, dropped = false;
  const TAB_JOB_MS = 30 * 60 * 1000;
  const FRAME_WAIT_MS = 30000;
  const COVER_WAIT_MS = 8000;                         // GOAL34 §2: no frame found a cover letter box by then: say so
  let coverWait = null;
  let fillStarted = 0;
  const CAREERS = /job|career|apply|talent|recruit|hiring|opening|position|vacanc/i;
  const P = (patch) => JH.panel.set(patch);
  const jobQuery = () => ({ uid: job && job.uid, app: job && job.app });

  function appLike() {
    const b = board();
    if (b.id !== "generic") return true;
    if ([...document.querySelectorAll("iframe")].some((f) => KNOWN_FRAMES.test(f.src || ""))) return true;
    if (document.querySelector("input[type=password]") && CAREERS.test(location.hostname + location.pathname)) return true;   // a careers site's Sign In
    return Boolean(document.querySelector("input[type=file]") && document.querySelector("input[type=email], input[name*=email i], input[autocomplete=email]"));
  }

  // GOAL30 §5: one match call per page address (the panel's first render, a frame's result and the toolbar button all
  // ask for it): the answer is shared while it is fresh
  const MATCH_MS = 30000;
  let matchMemo = null;
  function matchOnce(query) {
    const key = JSON.stringify(query);
    if (matchMemo && matchMemo.key === key && Date.now() - matchMemo.at < MATCH_MS) return matchMemo.got;
    const got = JH.api("GET", "/api/autofill/match", { query });
    matchMemo = { key, at: Date.now(), got };
    got.then((r) => { if (!r || !r.ok) matchMemo = null; });
    return got;
  }
  async function refresh() {
    const frames = [...document.querySelectorAll("iframe")].map((f) => f.src).filter((s) => KNOWN_FRAMES.test(s || "")).slice(0, 6);
    const got = await matchOnce({ url: location.href, frames: JSON.stringify(frames) });
    if (!got || !got.ok) return P({ loaded: true, error: (got && got.error) || "Job Hunt Agent did not answer" });
    job = got.data.job;
    if (!job && !dropped) {                               // an apply page reached from the posting matched in this tab
      const got2 = await JH.send({ type: "session:get" });
      const s = got2 && got2.ok ? got2.data : null;
      if (s && s.job && Date.now() - (s.jobAt || 0) < TAB_JOB_MS) return pick({ uid: s.job.uid, app: s.job.app }, false);
    }
    P({ loaded: true, error: null, job, recent: got.data.recent || [], resume: got.data.resume, address: got.data.address,
        sponsor: got.data.sponsor || null, openNeeds: got.data.needs, location: got.data.location || null });
    JH.send({ type: "broadcast", message: { type: "rowsPlease" } });
    if (job) await ownJob(job);
    pollAddress();
    matchScore();
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
        counts: { filled: sum("filled"), needs: needs.length + sum("held"), skipped: sum("skipped"), review: sum("review"),
                  notes: sum("held") ? `${sum("held")} address field(s) wait for the address above` : null },
        needs, rows: allRows() });
    lookUpAnswers();                                   // GOAL30 §2: the open questions it just filled hold text now
    JH.api("GET", "/api/autofill/count").then((r) => { if (r && r.ok) P({ openNeeds: r.data.open }); });
  }

  // Job Hunt Agent must be up before filling (the computer may be asleep or offline); says which version is current
  const OWN_VERSION = JH.version;
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
        lookUpAnswers();
      } else if (msg.type === "learned") {
        P({ learned: [...(JH.panel.get().learned || []), ...(msg.entries || [])].slice(-12) });
      } else if (msg.type === "confirmed") {
        session().then((s) => { if (s && !s.applied) markApplied(true); });
      } else if (msg.type === "note") {
        if (msg.cover) clearTimeout(coverWait);
        P({ message: msg.text });
      }
    },
  };

  // GOAL29 §5: Claude's answers to open questions, by question; looked up once per job (no Claude call), polled while written
  const asked = new Set();
  let askedFor = "";
  const setAnswer = (q, view) => P({ answers: { ...(JH.panel.get().answers || {}), [q]: view } });
  async function lookUpAnswers() {
    if (!job || !JH.answers) return;
    const key = `${job.uid || ""}|${job.app || ""}`;
    if (key !== askedFor) { asked.clear(); askedFor = key; P({ answers: {}, filled: {} }); }
    const questions = JH.answers.toLookUp(allRows(), asked);
    const filled = JH.answers.filledToCheck(allRows(), asked);       // GOAL30 §2: whose job this text was for
    if (!questions.length && !Object.keys(filled).length) return;
    questions.forEach((q) => asked.add(q));
    Object.entries(filled).forEach(([q, text]) => asked.add(`${q}\u0000${text}`));
    const got = await JH.api("POST", "/api/autofill/answer/lookup", { body: { ...jobQuery(), company: job.company || "", questions, filled } });
    if (!got || !got.ok) return;
    if (got.data.filled && Object.keys(got.data.filled).length) P({ filled: { ...(JH.panel.get().filled || {}), ...got.data.filled } });
    for (const [q, view] of Object.entries(got.data.answers || {})) { setAnswer(q, view); pollAnswer(q, view); }
  }
  function pollAnswer(q, view, n = 0) {
    const wait = JH.answers.waitFor(view);
    if (!wait || n >= JH.answers.MAX_POLLS) return;
    setTimeout(async () => {
      const mine = (JH.panel.get().answers || {})[q];
      if (!mine || mine.id !== view.id) return;             // Regenerate / Shorter started a newer one
      const got = await JH.api("GET", "/api/autofill/answer/status", { query: { id: view.id } });
      const next = got && got.ok ? got.data.answer : view;
      if (got && got.ok) setAnswer(q, next);
      pollAnswer(q, next, n + 1);
    }, wait);
  }

  async function pick(r, remember = true) {
    const got = remember ? await JH.api("POST", "/api/autofill/remember", { body: { url: location.href, uid: r.uid, app: r.app } })
      : await matchOnce({ url: location.href, uid: r.uid, app: r.app });
    if (remember) matchMemo = null;
    if (!got || !got.ok) return P({ loaded: true, message: (got && got.error) || "Could not save the pick" });
    resumeCache = null;
    if (remember) return refresh();
    job = got.data.job;
    P({ loaded: true, error: null, job: job && { ...job, how: "tab" }, resume: got.data.resume, address: got.data.address,
        sponsor: got.data.sponsor || null, openNeeds: got.data.needs, location: got.data.location || null });
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
    auto: (on) => { JH.save({ auto: on }); JH.send({ type: "session:set", patch: { auto: on } }); P({ auto: on }); },
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
    async generate(r, variant) {                            // GOAL29 §5: only on the user's click
      if (!job) return P({ message: "Pick the job first, then Generate" });
      const previous = variant ? ((JH.panel.get().answers || {})[r.label] || {}).text || r.value || undefined : undefined;   // GOAL30 §2: the text in the box
      setAnswer(r.label, { state: "writing", label: "Writing with Claude…", question: r.label });
      const got = await JH.api("POST", "/api/autofill/answer/generate", { body: { ...jobQuery(), question: r.label, max_chars: r.max, variant, previous } });
      if (!got || !got.ok) return setAnswer(r.label, { state: "failed", label: "No answer", error: (got && got.error) || "Job Hunt Agent did not answer" });
      setAnswer(r.label, got.data.answer);
      pollAnswer(r.label, got.data.answer);
    },
    fillAnswer(r, text) {                                   // into the page's box, shaded as Claude's: never Next / Submit
      JH.send({ type: "broadcast", message: { type: "setDraft", id: r.id, text } });
      P({ message: "Answer put in the box (shaded blue, solid outline): read it before you go on" });
    },
    async saveAnswer(r, text) {
      const got = await JH.api("POST", "/api/autofill/answer/save", { body: { ...jobQuery(), question: r.label, text } });
      P({ message: got && got.ok ? "Saved to Answers: offered for this question on other applications" : `Not saved: ${(got && got.error) || "Job Hunt Agent did not answer"}` });
    },
    focus: (n) => JH.send({ type: "broadcast", message: { type: "focus", id: n.id } }),
    settings: () => JH.send({ type: "openOptions" }),
    setField: (r, value) => JH.send({ type: "broadcast", message: { type: "setField", id: r.id, value } }),
    // GOAL30 §1.3 / §4: a chip or a ✎ answer: into the page field, and saved where the user chose (once, never re-learned)
    async keep(r, value, scope) {
      JH.send({ type: "broadcast", message: { type: "setField", id: r.id, value, explicit: true } });
      const got = await JH.api("POST", "/api/autofill/answer/keep", { body: { ...jobQuery(), label: r.label, value, scope, key: r.key,
        kind: r.kind, options: r.options || [], hints: r.hints || [], company: (job && job.company) || "", board: board().id } });
      const where = { job: "for this job only", company: `for ${(job && job.company) || "this company"} only`, everywhere: "for every application" }[scope];
      P({ message: got && got.ok ? `Saved ${where}: “${String(value).slice(0, 60)}”` : `Filled, not saved: ${(got && got.error) || "Job Hunt Agent did not answer"}` });
      JH.api("GET", "/api/autofill/count").then((c) => { if (c && c.ok) P({ openNeeds: c.data.open }); });
    },
    // GOAL30 §3: Location from the resume for this job, or where the user lives; this job only; the page's place fields follow
    async location(choice) {
      if (!job) return P({ message: "Pick the job first" });
      const got = await JH.api("POST", "/api/autofill/address/choose", { body: { ...jobQuery(), choice } });
      if (!got || !got.ok) return P({ message: `Not changed: ${(got && got.error) || "Job Hunt Agent did not answer"}` });
      matchMemo = null;
      P({ address: got.data.address, location: got.data.location });
      const a = got.data.answers || {};
      const value = (key) => (key === "location" ? a.location : a[key]) || "";
      const place = a.city ? { city: a.city, state: a.state || "", code: String(a.location || "").split(", ").pop() } : null;
      for (const r of allRows().filter((x) => PLACE_KEYS.has(x.key) && value(x.key))) {
        JH.send({ type: "broadcast", message: { type: "setField", id: r.id, value: value(r.key), explicit: true,
          place: r.kind === "combo" && (r.key === "city" || r.key === "location") ? place : null } });
      }
      P({ message: choice === "home" ? "Location: where you live, for this job only" : "Location: the city on your resume for this job" });
    },
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
    match: () => matchScore(true),
    async save() {                                          // GOAL27 §6: Save job from any page
      const got = await JH.api("POST", "/api/autofill/save", { body: { url: location.href, ...pageJob() } });
      if (!got || !got.ok) return P({ message: `Not saved: ${(got && got.error) || "Job Hunt Agent did not answer"}` });
      P({ saved: true, message: got.data.already ? "Already in your tracker" : `Saved to your tracker: ${got.data.company || ""} · ${got.data.role || ""}` });
      if (!job) refresh();
    },
    async cover() {                                         // GOAL27 §5: a cover letter only when the user asks
      if (!job) return P({ message: "Pick the job first, then Cover letter" });
      P({ coverBusy: true, message: "Writing a cover letter with Claude…" });
      const got = await JH.api("POST", "/api/autofill/cover", { body: jobQuery() });
      if (!got || !got.ok) return P({ coverBusy: false, message: `No cover letter: ${(got && got.error) || "Job Hunt Agent did not answer"}` });
      P({ coverBusy: false, cover: got.data, message: `Cover letter ready: Paste into the form, ${JH.noUpload() ? "" : "Upload as PDF, "}Download PDF, or Copy` });
    },
    coverInto: (mode) => {
      const cover = JH.panel.get().cover;
      if (!cover) return;
      P({ message: mode === "pdf" ? "Looking for the cover letter upload…" : "Looking for the cover letter box…" });
      clearTimeout(coverWait);
      coverWait = setTimeout(() => P({ message: mode === "pdf" ? "No cover letter upload found on this page: use Download PDF and drop it in"
        : "No cover letter box found on this page: use Copy, or Download PDF and drop it in" }), COVER_WAIT_MS);
      JH.send({ type: "broadcast", message: { type: "coverInto", mode, text: cover.text, job } });
    },
    // GOAL34 §1: the same PDFs Upload as PDF and the resume upload use, saved by Chrome under their upload names
    downloadCover: () => download("/api/autofill/cover.pdf", "No cover letter PDF"),
    downloadResume: () => download("/api/autofill/resume", "No resume PDF"),
  });

  async function download(path, failed) {
    if (!job) return P({ message: "Pick the job first" });
    const got = await JH.api("GET", path, { query: jobQuery(), binary: true });
    if (!got || !got.ok) return P({ message: `${failed}: ${(got && got.error) || "Job Hunt Agent did not answer"}` });
    JH.download(JH.b64File(got.data.b64, got.data.name, got.data.type || "application/pdf"));
    P({ message: `Downloaded ${got.data.name}: drag it from Chrome's downloads onto the upload box` });
  }

  // a page that reads like one job's description (long, with the words postings use)
  const postingLike = () => {
    const text = JH.text(document.querySelector("main, article, [class*=job i], body") || document.body);
    return text.length > 1500 && (text.match(/\b(responsibilities|requirements|qualifications|experience|benefits|about the (role|job|team))\b/gi) || []).length >= 3;
  };

  // the page's own words for the job (title, company, location, its text) when Job Hunt Agent does not know it
  function pageJob() {
    const meta = (n) => (document.querySelector(`meta[property="${n}"], meta[name="${n}"]`) || {}).content || "";
    const h1 = JH.text(document.querySelector("h1"));
    const title = meta("og:title") || h1 || document.title;
    const company = meta("og:site_name") || (/ at ([^|\-–]+)/.exec(document.title) || [])[1] || "";
    return { title: title.slice(0, 200), company: company.trim().slice(0, 200), text: JH.text(document.body).slice(0, 60000) };
  }

  // GOAL27 §6: match % and missing keywords on any job page (the matched job's JD, else this page's text)
  async function matchScore(asked = false) {
    const body = { ...jobQuery(), text: job ? "" : pageJob().text };
    if (!job && body.text.length < 400) { if (asked) P({ match: { error: "no job description on this page" } }); return; }
    const got = await JH.api("POST", "/api/autofill/score", { body });
    if (got && got.ok) P({ match: got.data });
    else if (asked) P({ match: { error: (got && got.error) || "Job Hunt Agent did not answer" } });
  }

  (async () => {
    const s = await JH.store(["profile", "auto"]);
    const name = s.profile || "your profile";
    P({ profileName: name[0].toUpperCase() + name.slice(1), auto: s.auto !== false, version: OWN_VERSION });
    const last = await JH.send({ type: "learn:last" });  // "Saved for next time" from the step the user just left
    if (last && last.ok && last.data && last.data.length) P({ learned: last.data });
    await JH.send({ type: "session:set", patch: { jobHost: null } });   // a new page: no auto-fill until its job is known
    const active = await session();
    // GOAL30 §5: a known job board's page (or a tab already applying) shows the panel at once and asks Job Hunt Agent in
    // parallel; any other page first waits for its own first render (hydration), as before
    const known = board().id !== "generic";
    if (!known || !active) await JH.settle(known ? 150 : 600, known ? 600 : 4000);
    if (appLike() || active) {
      JH.panel.show();
      healthy();
      await refresh();
    } else if (CAREERS.test(location.hostname + location.pathname)) {   // a posting page: matched quietly, so the
      const got = await JH.api("GET", "/api/autofill/match", { query: { url: location.href } });   // apply page after it knows the job
      if (got && got.ok && got.data.job) JH.send({ type: "session:set", patch: { job: got.data.job, jobAt: Date.now() } });
      if (got && got.ok && (got.data.job || postingLike())) {   // GOAL27 §6: the match % and Save job on the posting
        await JH.panel.peek();
        P({ loaded: true, job: got.data.job, recent: got.data.recent || [], resume: got.data.resume, sponsor: got.data.sponsor || null });
        job = got.data.job;
        matchScore();
      }
    }
  })();
})();

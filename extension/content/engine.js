// Job Hunt Agent Autofill 1.3, the fill engine (GOAL27): one frame's form, start to end, the way Simplify does it.
//  1. The profile, once: Job Hunt Agent turns the tailored resume + saved answers into one structured profile (contact,
//     links, work history with "• " bullets, education, skills, EEO, sponsorship, salary, start date, relocation).
//  2. Every control is classified (classify.js) into a canonical field before anything is touched.
//  3. The job's tailored resume PDF goes into the resume box (named by role); work history, education, skills and the
//     summary come from that resume; repeaters get one entry per job / school (Add another).
//  4. Every other field: Job Hunt Agent's answer for its canonical key (the user's saved words for this board and company first),
//     typed in like a person and checked; custom questions from the user's saved answers, then short ones from the resume
//     (Claude Haiku through Job Hunt Agent) with a "review" mark; long ones are offered in the panel, never filled.
//  5. Sensitive fields (SSN, birth date, ID / bank numbers) are never filled and never sent anywhere: the user's to type.
//  6. Each field ends filled / review / needs you / failed with why: a mark on the page, a row in the panel, a line in
//     Job Hunt Agent's per-field log. Never Next, never Submit.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const FIELD_MS = 12000;
  const CHOICE = /^(select|combo|listbox|radio|checkboxes|buttons)$/;
  const SKILL_MAX = 60;
  const CHIP = "[class*=chip], [class*=multi-value__label], [class*=multiValue] [class*=label], [class*=tag]:not(input), [class*=pill], [class*=token]";
  const LONG = /\bwhy\b|cover letter|tell us|describe|motivat|interest(ed)? in (this|the|working|joining)|explain|elaborate|share (an?|your)|walk us through/i;

  const need = (f, extra = {}) => ({ id: f.id, label: f.label, kind: f.kind, required: f.required,
    options: (extra.options || f.options || []).slice(0, 80), key: extra.key || null, why: extra.why || "" });

  // the words right around a control (its own small box, never a box holding other questions), e.g. Ashby's
  // "Autofill from resume" upload
  function nearText(el) {
    for (let node = el.parentElement, i = 0; node && i < 6; node = node.parentElement, i++) {
      if ([...node.querySelectorAll("input, select, textarea")].some((c) => c !== el && c.type !== "hidden")) return "";
      const text = JH.text(node);
      if (text.length > 300) return "";
      if (text.length > 8) return text;
    }
    return "";
  }

  function resumeField(b, fields) {
    const files = fields.filter((f) => f.kind === "file" && !/cover|transcript|portfolio|other|additional|autofill|auto-fill/i.test(`${f.label} ${nearText(f.el)}`) &&
      !(f.cls && /^(cover_letter_file|other_file)$/.test(f.cls.key)));
    const byKey = files.find((f) => f.cls && f.cls.key === "resume");
    const bySel = b.resume && files.find((f) => f.el.matches(b.resume));
    return byKey || bySel || (files.length === 1 ? files[0] : null);
  }

  const long = (f) => f.kind === "textarea" && (LONG.test(f.label) || f.label.length > 160);

  // the question's own box for a group of radios / checkboxes / buttons: its mark sits beside the whole question
  const anchorOf = (f) => (f.els ? f.el.closest("fieldset, [role=radiogroup], [role=group], .application-question, .field, .form-group") || f.el.parentElement
    : f.proxy || (f.kind === "combo" ? f.el.closest(".select__control, [class*=-control], [data-automation-id=multiSelectContainer]") : null) || null);

  class Run {
    constructor(b, ctx, root, skip) {
      this.b = b; this.ctx = ctx; this.root = root; this.skip = skip;
      this.results = new Map();                      // field id -> { f, status, why, key }
      this.out = { filled: 0, needs: [], skipped: 0, kept: 0, held: 0, review: 0, failed: 0, notes: [] };
      this.unmatched = [];                           // saved answers no option matched (Claude picks)
    }
    collect() { return JH.fields.collect(this.root).filter((f) => !(this.skip && this.skip(f.el))); }
    note(f, status, why = "") {
      this.results.set(f.id, { f, status, why, key: f.cls && f.cls.key });
      JH.marks.set(f.el, status === "kept" || status === "skipped" ? null : status, why, anchorOf(f));
      if (status === "filled") this.out.filled++;
      else if (status === "review") { this.out.filled++; this.out.review++; }
      else if (status === "kept") this.out.kept++;
      else if (status === "skipped") this.out.skipped++;
      else if (status === "failed") { this.out.failed++; this.out.needs.push(need(f, { why })); }
    }
    needs(f, extra = {}) {
      this.results.set(f.id, { f, status: "needs", why: extra.why || "", key: f.cls && f.cls.key });
      JH.marks.set(f.el, "needs", extra.why || "", anchorOf(f));
      this.out.needs.push(need(f, extra));
    }
    done(f) { return this.results.has(f.id); }
  }

  async function upload(run, fields) {
    const { b, ctx, out } = run;
    const rf = resumeField(b, fields);
    if (!rf || b.id === "workday") return false;
    if (rf.value) { run.note(rf, "kept", "a resume is already attached"); return false; }
    const file = await ctx.resumeFile();
    if (file && JH.setFile(rf.el, file)) {
      run.note(rf, "filled");
      out.notes.push(`Uploaded ${file.name}`);
      await JH.settle(500, (b.uploadWaitMs || 1500) + 2500);
      return true;
    }
    run.needs(rf, { why: ctx.resumeError || "no resume for this job" });
    return false;
  }

  // GOAL23 §2: a Skills box gets the tailored resume's skills: a typeahead one skill at a time, a plain box the list
  async function skills(run, boxes, data) {
    const list = (data && data.skills) || [];
    for (const f of boxes) {
      if (!list.length) { run.needs(f, { why: "no skills in the resume" }); continue; }
      if (f.kind !== "combo") {
        if (f.value || f.hand) { run.note(f, "kept"); continue; }
        run.note(f, (await JH.fill.putText(f.el, list.join(", "))) ? "filled" : "failed", "");
        continue;
      }
      let added = 0;
      const missing = [];
      const box = f.el.closest("[class*=field], [class*=Field], fieldset, .form-group") || f.el.parentElement;
      const chips = () => [...box.querySelectorAll(CHIP)].filter((c) => !c.closest("[role=listbox]")).map((c) => JH.pick.skillNorm(JH.text(c).replace(/[×✕x]$/, "")));
      for (const want of list.slice(0, SKILL_MAX)) {
        const before = chips();
        if (before.includes(JH.pick.skillNorm(want))) continue;
        const got = await JH.fill.skill(f.el, want, { skip: (t) => before.includes(JH.pick.skillNorm(t)) });
        if (got.ok && !got.already) added++; else if (!got.ok) missing.push(want);
      }
      JH.mark(f.el);
      run.note(f, added || chips().length ? "filled" : "needs", missing.length ? `not in the list: ${missing.slice(0, 8).join(", ")}` : "");
      if (missing.length) run.out.notes.push(`Skills not in the list: ${missing.join(", ")}`);
    }
  }

  // GOAL30 §0 (NVIDIA, 2026-10-20): a field inside a Work Experience / Education entry that the board's own code did
  // not reach (a layout it did not recognise) is filled here from the user's work-history bank, by its entry number, never
  // from the user's address: a past job's Location is the employer's country, a school's is its own city; "I currently work
  // here" is ticked only for the job the bank marks current. Any board.
  const WORK = { title: "title", company: "company", location: "country", country: "country", desc: "description",
    start: "start", end: "end" };
  const EDU = { school: "school", degree: "degree", field: "field", location: "location", city: "city", state: "state",
    country: "country", end: "end" };
  async function repeaters(run, boxes, data) {
    const nums = JH.classify.entries(boxes.map((f) => f.cls));        // "Work Experience 2" is the second job
    for (const [i, f] of boxes.entries()) {
      const { group, role } = f.cls;
      const list = (group === "work" ? data && data.jobs : data && data.schools) || [];
      const n = nums[i];
      const item = list[n];
      if (!item) { run.note(f, "skipped", `no ${group === "work" ? "job" : "school"} ${n + 1} in your work history`); continue; }
      if (f.hand || JH.byHand(f.el)) { run.note(f, "kept", "you filled it"); continue; }
      if (role === "current") {
        const want = group === "work" && Boolean(item.current);
        const box = f.els ? f.els[0] : f.el;
        if (box.checked !== want) JH.click(box);
        JH.mark(box);
        run.note(f, want ? "filled" : "skipped", want ? "" : "not your current job");
        continue;
      }
      if (group === "edu" && role === "start" && !f.required) { run.note(f, "skipped", "a start date only when the form needs one"); continue; }
      const map = group === "work" ? WORK : EDU;
      // a job with no country keeps its employer's place; a school's city never goes into a Country box
      const value = map[role] ? item[map[role]] || (group === "work" && map[role] === "country" ? item.location : "") : "";
      if (!value || (group === "work" && role === "end" && item.current)) {
        run.note(f, "skipped", group === "work" && /^(city|state)$/.test(role) ? "a past job: its country only" : "");
        continue;
      }
      let got;
      try {
        got = await JH.guard.run(async () => (run.b.fillField && (await run.b.fillField(f, { value, option: "" }))) ||
          JH.fill.field(f, { value, option: "" }, { overwrite: true }), FIELD_MS, "a field");
      } catch (err) { got = { status: "failed", why: String((err && err.message) || err).slice(0, 120) }; }
      if (got.status === "filled") run.note(f, "filled");
      else if (got.status === "kept") run.note(f, "kept", got.why);
      else run.needs(f, { options: got.options, why: got.why || "it did not take the value" });
    }
  }

  async function summary(run, boxes, data) {
    const text = String((data && data.summary) || "").trim();
    for (const f of boxes) {
      if (!text) continue;                             // a Summary nobody wrote: Job Hunt Agent's questions decide
      if (f.hand) { run.note(f, "kept", "you filled it"); continue; }
      if (f.value && f.value.trim() === text) { run.note(f, "filled"); continue; }
      run.note(f, (await JH.fill.putText(f.el, text)) ? "filled" : "failed", "");
    }
  }

  async function fillAnswer(run, f, ans, { review = false, why = "answered from your resume: read it" } = {}) {
    const { b } = run;
    if (!JH.guard.attempt(f.id)) { run.note(f, "skipped", "tried enough on this page"); return; }
    let got;
    try {
      const overwrite = typeof b.overwrite === "function" ? b.overwrite(f, ans) : Boolean(b.overwrite);
      got = await JH.guard.run(async () => (b.fillField && (await b.fillField(f, ans))) || JH.fill.field(f, ans, { overwrite }), FIELD_MS, "a field");
    } catch (err) {
      got = { status: "failed", why: String((err && err.message) || err).slice(0, 120) };
    }
    if (got.status === "filled") run.note(f, review ? "review" : "filled", review ? why : got.why || "");
    else if (got.status === "kept") run.note(f, "kept", got.why);
    else if (got.status === "skipped") run.note(f, "skipped", got.why);
    else if (got.status === "failed") run.note(f, "failed", got.why);
    else if (ans.value && (got.options || []).length && !review) run.unmatched.push({ f, ans, options: got.options });   // Claude picks next
    else run.needs(f, { options: got.options, key: ans.key, why: got.why || "no choice fits the saved answer" });
  }

  // 1.3.1 (AUTOFILL-BUGS #3): the user's saved answer matched none of a question's options by wording or meaning: Claude
  // picks the option that means the same, only from that list, marked ? review; still none -> Needs you
  async function picks(run) {
    const items = run.unmatched.splice(0);
    if (!items.length) return;
    const got = await JH.api("POST", "/api/autofill/choose", { body: { items: items.map(({ f, ans, options }) => ({
      id: f.id, question: f.label, answer: ans.value, options: options.slice(0, 60) })) } }).catch(() => null);
    const by = new Map(((got && got.ok && got.data.picks) || []).map((x) => [x.id, x.pick]));
    for (const { f, ans, options } of items) {
      const pick = by.get(f.id);
      if (pick) await fillAnswer(run, f, { ...ans, value: pick, option: pick }, { review: true, why: `your answer “${ans.value}” as this form says it: check it` });
      else run.needs(f, { options, key: ans.key, why: `none of its choices means “${ans.value}”` });
    }
  }

  // GOAL27 §4: questions no saved answer covers, short and not legal: answered from the resume, marked review
  async function suggestions(run, asked) {
    const { ctx } = run;
    const job = ctx.job || {};
    if (!asked.length || (!job.uid && !job.app)) return asked;
    const { suggest } = await JH.store(["suggest"]);
    if (suggest === false) return asked;
    const qs = [];
    for (const { f } of asked) {
      const options = CHOICE.test(f.kind) ? (f.options && f.options.length ? f.options : await JH.fill.readOptions(f).catch(() => [])) : [];
      f.options = options;
      qs.push({ id: f.id, label: f.label, kind: f.kind === "combo" || f.kind === "listbox" || f.kind === "buttons" ? "select" : f.kind,
                options: options.slice(0, 40), key: f.cls && f.cls.key, hints: f.hints });
    }
    const got = await JH.api("POST", "/api/autofill/suggest", { body: { uid: job.uid, app: job.app, questions: qs } });
    if (!got || !got.ok) return asked;
    const by = new Map(got.data.answers.map((a) => [a.id, a]));
    const left = [];
    for (const a of asked) {
      const s = by.get(a.f.id);
      if (s && s.answer) await fillAnswer(run, a.f, { value: s.answer, option: "", key: null }, { review: true });
      else left.push(a);
    }
    return left;
  }

  JH.engine = {
    async run(b, ctx, { skip } = {}) {
      if (JH.captcha() === "challenge") return { captcha: true, filled: 0, needs: [], skipped: 0 };
      const run = new Run(b, ctx, b.root ? b.root() : document, skip);
      const { out } = run;
      // 1.3.1 #6: a Sign In / Create Account form gets the user's job-site account (from the extension's settings only)
      const acct = b.id === "workday" ? null : await JH.account.fill(run.root).catch(() => null);
      if (acct) { out.filled += acct.filled; out.notes.push(acct.message); }
      let fields = run.collect();
      if (await upload(run, fields)) fields = run.collect();
      for (const f of fields) {                      // filled before (an earlier Autofill on this step)
        if (f.kind !== "file" && f.el.hasAttribute("data-jh-filled") && f.value && !run.done(f)) run.note(f, JH.marks.get(f.el) === "review" ? "review" : "filled");
      }
      const data = await ctx.profileData();
      // work history and education, one entry per job / school of the resume this job uploads
      let parts = new Set();
      if (b.id !== "workday" && data && JH.experience.wanted(run.root)) {
        try {
          const xp = await JH.experience.fill(run.root, data);
          parts = xp.handled;
          fields = run.collect();
          for (const f of fields) {
            if (!parts.has(f.el) || run.done(f)) continue;
            run.note(f, f.value || f.el.hasAttribute("data-jh-filled") ? "filled" : "skipped", f.value ? "" : "no value in your resume");
          }
        } catch (err) {
          JH.guard.report("error", `work history / education: ${String((err && err.message) || err).slice(0, 300)}`, b, b.stepLabel ? b.stepLabel() : "");
        }
      }
      const open = fields.filter((f) => !run.done(f) && !parts.has(f.el) && f.kind !== "file" && !f.el.hasAttribute("data-jh-filled"));
      await skills(run, open.filter((f) => f.cls && f.cls.key === "skills" && !f.hand), data);
      await summary(run, open.filter((f) => f.cls && f.cls.key === "summary" && !run.done(f)), data);
      if (data) await repeaters(run, open.filter((f) => f.cls && f.cls.group && !run.done(f) && f.kind !== "file"), data);
      for (const f of open) {                        // never filled, never sent: the user's to type
        if (f.cls && f.cls.key === "sensitive" && !run.done(f)) {
          if (f.value || f.hand) run.note(f, "kept"); else run.needs(f, { why: "sensitive: yours to type" });
        }
      }
      const asked = open.filter((f) => !run.done(f));
      for (const f of fields) if (f.kind === "file" && !run.done(f) && f.cls && f.cls.key !== "resume") run.note(f, "skipped", "");
      if (!asked.length) return finish(run);
      const job = ctx.job || {};
      const res = await JH.api("POST", "/api/autofill/resolve", { body: {
        fields: asked.map(JH.fields.describe), board: b.id, company: job.company || "", section: b.section ? b.section() : "",
        uid: job.uid, app: job.app, source_url: job.url || "" } });
      if (!res || !res.ok) { out.error = (res && res.error) || "Job Hunt Agent did not answer"; return finish(run); }
      const byId = new Map(asked.map((f) => [f.id, f]));
      const unanswered = [];
      for (const ans of res.data.answers) {
        const f = byId.get(ans.id);
        if (!f) continue;
        f.el.removeAttribute("data-jh-need");
        f.el.removeAttribute("data-jh-hold");
        if (ans.status === "skip") { run.note(f, "skipped"); continue; }
        if (ans.status === "hold") { out.held++; f.el.setAttribute("data-jh-hold", ""); run.results.set(f.id, { f, status: "needs", why: "waits for the address" }); continue; }
        if (ans.key || ans.origin) JH.fields.note(f.el, { key: ans.key || null, origin: ans.origin || null });   // the panel's row
        if (ans.status === "needs") { unanswered.push({ f, ans }); continue; }
        // GOAL30 §1: Claude read the wording as a known field / saved question: filled, marked ? review
        await fillAnswer(run, f, ans, ans.review ? { review: true, why: ans.why || "Claude read this question: check it" } : {});
      }
      await picks(run);
      // the user's saved answers had nothing: short, non-legal questions from the resume; long ones are offered, not filled
      // GOAL30 §1.4: never a legal or personal fact (employment, health care provider, salary, start date, EEO): the user's
      // to pick from the panel's choices
      const short = unanswered.filter(({ f, ans }) => !ans.legal && !ans.personal && !long(f) && (f.required || CHOICE.test(f.kind)) && f.kind !== "checkbox");
      let left = [...unanswered.filter((u) => !short.includes(u))];
      try { left = left.concat(await suggestions(run, short)); } catch { left = left.concat(short); }
      for (const { f, ans } of left) {
        if (run.done(f) && run.results.get(f.id).status !== "needs") continue;
        const options = f.options && f.options.length ? f.options : await JH.fill.readOptions(f).catch(() => []);
        run.needs(f, { ...ans, options, why: long(f) ? "a long answer: Draft it from the panel" : "no saved answer" });
      }
      return finish(run);
    },
  };

  function finish(run) {
    const { out } = run;
    out.message = out.notes.join(" · ");
    out.fields = [...run.results.values()].map((r) => ({ label: r.f.label, kind: r.f.kind, key: r.key || null, status: r.status, why: r.why || "" }));
    return out;
  }
})();

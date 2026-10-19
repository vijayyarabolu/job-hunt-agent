// Job Hunt Agent Autofill board: Workday (the deep one). Steps: Create Account / Sign In, My Information, My Experience,
// Application Questions, Voluntary Disclosures, Self Identify, Review. Account: create (email, password, verify,
// terms box) or, when Workday says the account exists, switch to Sign In and fill the same email / password; the
// person clicks Create Account / Sign In. My Experience: the tailored resume is uploaded, then Workday's parsed Work
// Experience / Education / Websites are corrected to the person's real history (wrong rows removed, missing ones
// added) and the Skills box gets the tailored resume's skills. Nothing on Review; never Next, never Submit.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];

(() => {
  const A = (id) => `[data-automation-id="${id}"]`;
  const ADDRESS_KEYS = new Set(["address1", "address2", "city", "state", "zip", "location"]);   // §7: this resume's city
  const STEPS = [[/create account|sign in/i, "auth"], [/my information|contact information/i, "info"],
    [/my experience|experience/i, "experience"], [/question/i, "questions"], [/voluntary disclosure/i, "disclosures"],
    [/self.?identif|disability/i, "self_identify"], [/review/i, "review"]];
  const PAGES = { applyFlowMyInfoPage: "info", applyFlowMyExpPage: "experience", applyFlowPrimaryQuestionsPage: "questions",
    applyFlowSecondaryQuestionsPage: "questions", applyFlowVoluntaryDisclosuresPage: "disclosures",
    applyFlowSelfIdentifyPage: "self_identify", applyFlowReviewPage: "review", applyFlowEEOPage: "disclosures" };
  const VERIFY = /verify your (e-?mail|account)|verification (e-?mail|link)|check your e-?mail|confirm your e-?mail/i;
  const EXISTS = /already (exists|in use|registered|associated)|account (with this e-?mail )?already|email address is already/i;
  const DONE = /application (has been |was )?(successfully )?submitted|thank(s| you) for (applying|your application)|congratulations.{0,40}(submitted|applied)|you have (successfully )?applied/i;

  const q = (sel, root = document) => root.querySelector(sel);
  const qa = (sel, root = document) => [...root.querySelectorAll(sel)];

  function step() {
    if (!q(`input${A("password")}`) && VERIFY.test(JH.text(q("main") || document.body).slice(0, 3000))) return "verify";
    if (q(A("signInContent")) || (q(A("email")) && q(A("password"))) || q(A("SignInWithEmailButton"))) return "auth";
    for (const [id, name] of Object.entries(PAGES)) if (q(A(id))) return name;
    const active = JH.text(q(A("progressBarActiveStep"))).replace(/current step \d+ of \d+/i, "");
    const hit = STEPS.find(([rx]) => rx.test(active));
    return hit ? hit[1] : "unknown";
  }

  function stepLabel() {
    return JH.text(q(A("progressBarActiveStep"))).replace(/current step \d+ of \d+/i, "").trim() || step();
  }

  // ---- account ----------------------------------------------------------------------------------------------
  async function account(ctx) {
    const { account: acc } = await JH.store(["account"]);
    if (!acc || !acc.email || !acc.password) {
      return { filled: 0, message: "Add your job-site email and password in the extension's settings, then click Autofill" };
    }
    const chooser = q(A("SignInWithEmailButton"));       // "Sign in with Google / LinkedIn / email": email (a choice, not a sign-in)
    if (chooser && !q(`input${A("email")}`)) {
      JH.click(chooser, { allow: true });
      await JH.waitFor(() => q(`input${A("email")}`), 5000);
    }
    const errors = qa(`${A("errorMessage")}, [role=alert]`).map(JH.text).join(" ");
    const creating = Boolean(q(A("verifyPassword")));
    if (creating && EXISTS.test(errors)) {
      const link = q(A("signInLink"));
      if (link) {
        JH.click(link, { allow: true });                 // switch the form to Sign In (not a submit)
        await JH.waitFor(() => !q(A("verifyPassword")) && q(A("password")), 4000);
      }
    }
    let filled = 0;
    const set = (id, value) => {                         // counted when it holds the account, set now or before
      const el = q(`input${A(id)}`);
      if (!el || JH.byHand(el)) return;                  // what the user typed for this site stays
      if (el.value !== value) JH.setValue(el, value);
      if (el.value === value) filled++;
    };
    set("email", acc.email);
    set("password", acc.password);
    if (q(A("verifyPassword"))) {
      set("verifyPassword", acc.password);
      const box = q(`input${A("createAccountCheckbox")}`);
      if (box && !box.checked) JH.click(box);
      if (box && box.checked) filled++;
      return { filled, message: "Click Create Account. If Workday says the account exists, click Autofill again" };
    }
    return { filled, message: "Click Sign In" };
  }

  // ---- Workday widgets ----------------------------------------------------------------------------------------
  async function datePart(input, value) {
    if (!input || !value) return false;
    JH.setValue(input, value);
    await JH.sleep(60);
    if (String(Number(input.value)) !== String(Number(value))) {
      await JH.react(input, "dateUp", value);
      JH.click(input);
    }
    return String(Number(input.value)) === String(Number(value));
  }

  // one filler per question: a step that timed out may still be filling when the plain filler starts
  const setDate = (container, mmYyyy) => JH.fill.once(container, () => setDateNow(container, mmYyyy), false);
  const prompt = (input, answers) => JH.fill.once(input, () => promptNow(input, answers), { status: "kept", why: "it is being filled" });

  async function setDateNow(container, mmYyyy) {
    // "06/2026" or "MM/DD/YYYY" into Workday's Month / Day / Year parts (or a plain text box)
    const parts = String(mmYyyy || "").split("/");
    if (!container || parts.length < 2) return false;
    const [mm, dd, yyyy] = parts.length === 3 ? parts : [parts[0], null, parts[1]];
    const month = q(`${A("dateSectionMonth-input")}, input[aria-label=Month]`, container);
    const day = q(`${A("dateSectionDay-input")}, input[aria-label=Day]`, container);
    const year = q(`${A("dateSectionYear-input")}, input[aria-label=Year]`, container);
    if (!month && !year) {
      const box = q("input[type=text], input:not([type])", container);
      return box ? JH.setValue(box, mmYyyy) : false;
    }
    let ok = true;
    if (month) ok = (await datePart(month, mm)) && ok;
    if (day && dd) ok = (await datePart(day, dd)) && ok;
    if (year) ok = (await datePart(year, yyyy)) && ok;
    return ok;
  }

  async function promptNow(input, answers) {
    // Workday's searchable "prompt" (How did you hear, Country phone code, Skills, School, Field of study)
    const box = input.closest(`${A("multiSelectContainer")}, ${A("multiselectInputContainer")}`) || input.parentElement;
    const chosen = () => qa(`${A("selectedItem")}, ${A("selectedItemList")} li`, box.closest("[data-automation-id^=formField]") || box).map(JH.text);
    if (chosen().some((t) => JH.pick.best([t], answers))) return { status: "filled", already: true };
    const query = String(answers[answers.length - 1]).slice(0, 40);
    await JH.type(input, query);
    JH.key(input, "Enter");
    let got = await JH.fill.chooseFrom(input, answers);
    if (!got.ok) {
      await JH.react(input, "keyEnter", query);
      got = await JH.fill.chooseFrom(input, answers);
    }
    if (!got.ok) { JH.key(input, "Escape"); return { status: "needs", options: got.options, why: "no choice fits the saved answer" }; }
    await JH.waitFor(() => chosen().length, 1500);
    JH.mark(input);
    JH.key(input, "Escape");
    return { status: "filled", text: got.text };
  }

  // the board's own handling of a field (else the shared filler)
  async function fillField(f, ans) {
    const answers = [ans.option, ans.value].filter((a) => a != null && String(a).trim() !== "");
    if (f.kind === "combo" && f.el.closest(`${A("multiSelectContainer")}, ${A("multiselectInputContainer")}`) || f.el.matches(A("searchBox"))) {
      if (f.hand || JH.byHand(f.el)) return { status: "kept" };
      return prompt(f.el, answers);
    }
    return null;
  }

  // dates Workday asks outside My Experience, in Month / Day / Year parts: Job Hunt Agent answers them like any question
  // (Self Identify's "Date": today, once a signature is saved; "Earliest start date" saved as "2 weeks after offer
  // acceptance": today + 2 weeks, worked out now); no answer -> Needs you
  const DATE_PARTS = `${A("dateSectionMonth-input")}, input[aria-label=Month], ${A("dateSectionYear-input")}, input[aria-label=Year]`;
  let dateN = 0;
  async function dateFields(ctx, res) {
    const boxes = qa("[data-automation-id^=formField-]").filter((field) => q(DATE_PARTS, field) &&
      !field.closest("[data-automation-id^=workExperience], [data-automation-id^=education], [role=group][aria-label]") &&
      !qa("input", field).some((i) => JH.byHand(i) || (i.value && !i.hasAttribute("data-jh-filled"))));
    if (!boxes.length) return;
    const asked = boxes.map((field) => {
      const first = q(DATE_PARTS, field);
      if (!first.dataset.jhId) first.dataset.jhId = `wdd${++dateN}`;
      const label = JH.text(q("label, legend", field)).replace(/\*\s*$/, "").trim();
      return { field, first, d: { id: first.dataset.jhId, kind: "date", label, required: /\*/.test(JH.text(q("label, legend", field))) ||
        Boolean(q("[aria-required=true]", field)), options: [], section: "",
        hints: [String(field.getAttribute("data-automation-id")).replace(/^formField-/, "").toLowerCase()] } };
    });
    const job = ctx.job || {};
    const got = await JH.api("POST", "/api/autofill/resolve", { body: { fields: asked.map((a) => a.d), board: "workday",
      company: job.company || "", section: ({ disclosures: "disclosures", self_identify: "self_identify" })[step()] || step(),
      uid: job.uid, app: job.app, source_url: job.url || "" } });
    if (!got || !got.ok) return;
    for (const ans of got.data.answers) {
      const a = asked.find((x) => x.d.id === ans.id);
      if (!a) continue;
      if (ans.status === "skip") { res.skipped = (res.skipped || 0) + 1; continue; }
      const value = String(ans.value || "").trim();
      const ends = /^\d{1,2}\/(\d{1,2}\/)?\d{4}$/.test(value) ? null : JH.pick.waitDate(value);
      const date = ends ? `${ends.mm}/${ends.dd}/${ends.yyyy}` : value;
      if (ans.status === "fill" && date && (await setDate(a.field, date))) {
        qa(DATE_PARTS, a.field).forEach((i) => JH.mark(i));
        res.filled++;
      } else res.needs.push({ id: a.d.id, label: a.d.label, kind: "date", key: ans.key });
    }
  }

  // ---- My Experience ----------------------------------------------------------------------------------------
  function sectionPanels(word) {
    // "Work Experience 1", "Education 2", "Websites 1": Workday's repeating panels
    const groups = qa("[role=group], fieldset, [data-automation-id^=workExperience-], [data-automation-id^=education-], [data-automation-id^=websitePanelSet-], [data-automation-id^=website-]")
      .filter((g) => {
        const id = g.getAttribute("data-automation-id") || "";
        const name = (g.getAttribute("aria-label") || JH.text(q("h3, h4, h5, legend", g)) || "").trim();
        if (word === "work") return /^workExperience-\d+$/.test(id) || /^work experience \d+$/i.test(name);
        if (word === "education") return /^education-\d+$/.test(id) || /^education \d+$/i.test(name);
        return /^(websitePanelSet|website)-\d+$/.test(id) || /^websites? \d+$/i.test(name);
      });
    return groups.filter((g) => !groups.some((o) => o !== g && o.contains(g)));
  }

  function sectionOf(word) {
    const title = word === "work" ? /^work experience$/i : word === "education" ? /^education$/i : /^websites?$/i;
    const heading = qa("h2, h3, h4, legend, [role=heading]").find((h) => title.test(JH.text(h)));
    const auto = q(A(word === "work" ? "workExperienceSection" : word === "education" ? "educationSection" : "websiteSection"));
    return auto || (heading && heading.closest("[role=group], section, fieldset, div[data-automation-id]")) || (heading && heading.parentElement);
  }

  function addButton(word) {
    const sec = sectionOf(word);
    const scope = sec || document;
    return qa("button", scope).find((b) => /^add( another)?$/i.test(JH.text(b)) || /^(add|add-button)$/.test(b.getAttribute("data-automation-id") || ""));
  }

  function inPanel(panel, names, labelRx) {
    for (const name of names) {
      const el = q(`input${A(name)}, textarea${A(name)}, input[name="${name}"], textarea[name="${name}"], [id$="--${name}"]`, panel);
      if (el) return el;
    }
    const field = qa("[data-automation-id^=formField-]", panel).find((f) => labelRx.test(JH.text(q("label, legend", f))));
    return field ? q("input:not([type=checkbox]):not([type=radio]), textarea, button[aria-haspopup=listbox]", field) : null;
  }

  function fieldBox(panel, names, labelRx) {
    for (const name of names) {
      const el = q(`[data-automation-id="formField-${name}"]`, panel);
      if (el) return el;
    }
    return qa("[data-automation-id^=formField-]", panel).find((f) => labelRx.test(JH.text(q("label, legend", f)))) || null;
  }

  async function text(el, value, out) {
    if (!el || value == null) return;
    if (JH.byHand(el)) { out.kept++; return; }
    if (el.tagName === "BUTTON") {
      const got = await JH.fill.field({ kind: "listbox", el, value: "", hand: false }, { value });
      if (got.status === "filled") out.filled++; else out.needs.push({ label: JH.labelOf(el), kind: "select", options: got.options || [] });
      return;
    }
    if (el.closest(`${A("multiSelectContainer")}, ${A("multiselectInputContainer")}`)) {
      const got = await prompt(el, [value]);
      if (got.status === "filled") out.filled++; else out.needs.push({ label: JH.labelOf(el), kind: "select", options: got.options || [] });
      return;
    }
    if (el.value !== value) JH.setValue(el, value);
    out.filled++;
  }

  async function ensurePanels(word, count) {
    for (let guard = 0; sectionPanels(word).length < count && guard < 8; guard++) {
      const before = sectionPanels(word).length;
      const add = addButton(word);
      if (!add) break;
      JH.click(add);
      if (!(await JH.waitFor(() => sectionPanels(word).length > before, 3000))) break;
      await JH.settle(250, 2000);
    }
    let panels = sectionPanels(word);
    for (let guard = 0; panels.length > count && guard < 8; guard++) {     // a wrong parsed row: removed
      const extra = panels[panels.length - 1];
      const del = qa("button", extra).find((b) => /delete|remove/i.test(b.getAttribute("aria-label") || JH.text(b) || "") ||
        /panel-set-delete-button|delete/i.test(b.getAttribute("data-automation-id") || ""));
      if (!del) break;
      JH.click(del);
      await JH.waitFor(() => sectionPanels(word).length < panels.length, 3000);
      panels = sectionPanels(word);
    }
    return sectionPanels(word);
  }

  async function workHistory(jobs, out) {
    if (!jobs.length) return;
    const panels = await ensurePanels("work", jobs.length);
    for (let i = 0; i < Math.min(panels.length, jobs.length); i++) {
      const p = panels[i], job = jobs[i];
      await text(inPanel(p, ["jobTitle"], /job title/i), job.title, out);
      await text(inPanel(p, ["companyName", "company"], /^company/i), job.company, out);
      if (job.location) await text(inPanel(p, ["location"], /^location/i), job.location, out);
      const current = q(`input${A("currentlyWorkHere")}, input[type=checkbox][name*=currently i], input[type=checkbox][id*=currentlyWorkHere]`, p) ||
        qa("input[type=checkbox]", p).find((c) => /currently work/i.test(JH.labelOf(c)));
      if (current && current.checked !== Boolean(job.current) && !JH.byHand(current)) { JH.click(current); out.filled++; await JH.settle(200, 1500); }
      if (await setDate(fieldBox(p, ["startDate"], /^from|start/i), job.start)) out.filled++;
      if (!job.current && job.end && (await setDate(fieldBox(p, ["endDate"], /^to\b|^to$|end/i), job.end))) out.filled++;
      const desc = inPanel(p, ["roleDescription", "description"], /role description|description/i);   // the job's "• " bullets
      if (desc && job.description && !JH.byHand(desc)) { JH.setValue(desc, job.description); JH.mark(desc); out.filled++; }
    }
  }

  async function education(schools, out) {
    if (!schools.length) return;
    const panels = await ensurePanels("education", schools.length);
    for (let i = 0; i < Math.min(panels.length, schools.length); i++) {
      const p = panels[i], s = schools[i];
      await text(inPanel(p, ["school", "schoolName"], /school|university|institution/i), s.school, out);
      await text(inPanel(p, ["degree"], /degree/i), s.degree, out);
      await text(inPanel(p, ["fieldOfStudy", "field-of-study"], /field of study|major/i), s.field, out);
      const from = fieldBox(p, ["firstYearAttended", "startDate"], /^from|start|first year/i);   // the rule: a start date only when required
      if (from && q("abbr, [aria-required=true], [required]", from) && (await setDate(from, s.start))) out.filled++;
      if (await setDate(fieldBox(p, ["lastYearAttended", "endDate"], /^to\b|^to|end|last year|graduat/i), s.end)) out.filled++;
    }
  }

  async function websites(links, out) {
    // exactly the user's links: one row each, a wrong parsed row corrected, a junk row deleted
    const wanted = [links.linkedin, links.github, links.portfolio].filter(Boolean);
    if (!wanted.length || !sectionOf("websites")) return;
    const norm = (u) => String(u || "").trim().replace(/\/+$/, "").toLowerCase();
    const panels = await ensurePanels("websites", wanted.length);
    const values = panels.map((p) => norm((q("input", p) || {}).value));
    const left = wanted.filter((u) => !values.includes(norm(u)));        // rows already right stay as they are
    for (let i = 0; i < panels.length; i++) {
      const input = q("input", panels[i]);
      if (input && !wanted.some((u) => norm(u) === values[i]) && left.length) await text(input, left.shift(), out);
    }
  }

  async function skills(list, out) {
    // (user 14:24): every skill of the uploaded tailored resume, one at a time through Workday's typeahead
    const field = fieldBox(document, ["skills"], /skills/i);
    const input = field && q(`input${A("searchBox")}, input`, field);
    if (!input || !list.length) return;
    const have = () => qa(`${A("selectedItem")}, ${A("selectedItemList")} li`, field).map((x) => JH.pick.skillNorm(x.getAttribute("title") || JH.text(x).replace(/×$/, "")));
    let added = 0;
    const missing = [];
    for (const want of list.slice(0, 60)) {
      const before = have();
      if (before.includes(JH.pick.skillNorm(want))) continue;                       // already there
      const got = await JH.fill.skill(input, want, { enter: true, skip: (t) => before.includes(JH.pick.skillNorm(t)) });
      if (!got.ok) { missing.push(want); continue; }
      if (got.already) continue;                         // "AWS" when "Amazon Web Services (AWS)" is in: never clicked again
      if (await JH.waitFor(() => have().length > before.length, 1500)) added++;
      else missing.push(want);
    }
    JH.mark(input);
    out.filled += added;
    out.notes.push(`Skills: ${added} added${missing.length ? ` · not in Workday's list: ${missing.join(", ")}` : ""}`);
  }

  async function upload(ctx, out) {
    const input = q(`input[type=file]${A("file-upload-input-ref")}`) || q("input[type=file]");
    if (!input) return false;
    const box = input.closest("[data-automation-id^=formField-]") || document;
    if (q(`${A("file-upload-successful")}, ${A("file-upload-item")}`, box)) { out.notes.push("A resume is already attached: kept"); return false; }
    const file = await ctx.resumeFile();
    if (!file) { out.needs.push({ label: "Resume", kind: "file", why: ctx.resumeError || "no resume" }); return false; }
    JH.setFile(input, file);
    if (!(await JH.waitFor(() => q(`${A("file-upload-successful")}, ${A("file-upload-item")}`, box), 4000))) {
      const zone = q(A("file-upload-drop-zone"), box);
      if (zone) await JH.react(zone, "drop");
    }
    const ok = await JH.waitFor(() => q(`${A("file-upload-successful")}, ${A("file-upload-item")}`, box), 8000);
    if (ok) { out.filled++; out.notes.push(`Uploaded ${file.name}`); }
    else out.needs.push({ label: "Resume", kind: "file", why: "Workday did not take the file: drop it in yourself" });
    return Boolean(ok);
  }

  async function experience(ctx) {
    const out = { filled: 0, kept: 0, needs: [], notes: [] };
    if (await upload(ctx, out)) await JH.settle(1500, 20000);          // Workday parses the resume into the page
    const data = await ctx.profileData();
    if (data) {
      await workHistory(data.jobs || [], out);
      await education(data.schools || [], out);
      await websites(data.links || {}, out);
      await skills(data.skills || [], out);
    }
    const handled = (el) => el.closest("[data-automation-id^=workExperience], [data-automation-id^=education], [data-automation-id^=website], [role=group][aria-label]") ||
      (fieldBox(document, ["skills"], /^skills/i) || { contains: () => false }).contains(el);
    const rest = await ctx.generic({ skip: handled });
    return { filled: out.filled + rest.filled, needs: [...out.needs, ...rest.needs], skipped: rest.skipped,
             kept: out.kept + (rest.kept || 0), held: rest.held, message: out.notes.join(" · ") || rest.message };
  }

  JH.boards.push({
    // a value Workday carried over (an old application's city) gives way only to the address of this resume's city
    id: "workday", name: "Workday", overwrite: (f, ans) => ADDRESS_KEYS.has(ans && ans.key),
    match: (url, doc) => /(^|\.)myworkdayjobs\.com$|(^|\.)myworkdaysite\.com$|(^|\.)workday\.com$/.test(new URL(url).hostname) ||
      Boolean(doc.querySelector('[data-automation-id="applyFlowPage"], [data-automation-id="signInContent"], [data-automation-id="jobPostingPage"]')),
    step, stepLabel,
    // never learned from the account step, the resume history (it comes from the user's profile) or Review
    noLearn: (el) => ["auth", "verify", "experience", "review"].includes(step()) ||
      Boolean(el.closest("[data-automation-id^=workExperience], [data-automation-id^=education], [data-automation-id^=website]")),
    unknown: () => step() === "unknown" && Boolean(q(A("applyFlowPage"))),       // a step this adapter does not know
    section: () => ({ disclosures: "disclosures", self_identify: "self_identify" })[step()] || step(),
    signature: () => `${step()}|${stepLabel()}|${qa("[data-automation-id^=formField-]").length}`,
    fillField,
    confirmed: () => DONE.test(JH.text(q(`${A("congratulationsPopup")}, [role=dialog], main`) || document.body).slice(0, 4000)),
    async fill(ctx) {
      const s = step();
      if (s === "auth") return account(ctx);
      if (s === "verify") return { filled: 0, message: "Check your email, then click Autofill again" };
      if (s === "review") return { filled: 0, message: "Review, then click Submit" };
      if (q(A("jobPostingPage")) && !q(A("applyFlowPage"))) return { filled: 0, message: "Click Apply on the posting, then Autofill" };
      if (s === "experience") return experience(ctx);
      const res = await ctx.generic({});
      await dateFields(ctx, res);
      return res;
    },
  });
})();

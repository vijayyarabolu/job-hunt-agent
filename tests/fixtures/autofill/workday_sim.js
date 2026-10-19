// GOAL23 test fixture: Workday's widgets in plain JS, for the Autofill extension's browser tests. Pages describe
// fields as <div data-wd="kind" ...>; this builds Workday's DOM for them (automation ids, labels, listbox buttons,
// searchable prompts, Month/Day/Year parts, repeating panels with Add / Delete, the resume drop zone that "parses"
// the file into wrong rows). Next / Submit / Create Account / Sign In only act on a person's (trusted) click; every
// click on them is logged in window.__wd.clicks so the test can prove the extension never made one.
"use strict";
(() => {
  const W = (window.__wd = window.__wd || { clicks: [], steps: [] });
  let seq = 100;
  const nid = () => `input-${++seq}`;
  const h = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) if (v != null && v !== false) n.setAttribute(k, v === true ? "" : v);
    for (const kid of kids.flat()) if (kid != null) n.append(kid.nodeType ? kid : String(kid));
    return n;
  };
  const label = (text, forId, req) => h("label", { for: forId }, h("span", {}, text, req ? h("abbr", { "aria-hidden": "true", title: "required" }, "*") : null));
  const field = (auto, ...kids) => h("div", { "data-automation-id": `formField-${auto}` }, ...kids);
  const STYLE = "body{font:14px sans-serif;margin:0}main{padding:16px 24px 160px}[data-automation-id^=formField-]{margin:10px 0}" +
    "label{display:block;font-weight:600;margin-bottom:3px}input[type=text],textarea{width:320px;padding:5px}button{padding:5px 10px;margin:2px}" +
    "[data-automation-id=activeListContainer]{position:absolute;background:#fff;border:1px solid #888;z-index:50;max-height:260px;overflow:auto;width:340px}" +
    "[role=option]{padding:4px 8px;cursor:pointer}[role=option]:hover{background:#eef}[role=group]{border:1px solid #ccc;padding:8px;margin:8px 0}" +
    "[data-automation-id=dateInputWrapper] input{width:46px}.wd-foot{position:fixed;bottom:0;left:0;right:0;background:#f4f4f4;padding:10px 24px}" +
    "[data-automation-id=selectedItem]{display:inline-block;background:#ddeeff;border-radius:10px;padding:1px 8px;margin:2px}";

  // ---- popups -------------------------------------------------------------------------------------------------
  function closePopup() { document.querySelectorAll("[data-automation-id=activeListContainer]").forEach((p) => p.remove()); }
  function popup(anchor, options, onPick, prompt) {
    closePopup();
    const r = anchor.getBoundingClientRect();
    const list = h("div", { "data-automation-id": "activeListContainer", role: "listbox" });
    list.style.left = `${r.left + scrollX}px`;
    list.style.top = `${r.bottom + scrollY + 2}px`;
    for (const o of options) {
      const opt = h("div", { role: "option", "aria-selected": "false", "data-automation-id": prompt ? "promptOption" : "menuItem",
        "data-automation-label": o, tabindex: "-1" }, h("div", {}, o));
      opt.addEventListener("click", (e) => { e.stopPropagation(); onPick(o); closePopup(); });
      list.append(opt);
    }
    document.body.append(list);
  }
  document.addEventListener("click", (e) => { if (!e.target.closest("[data-automation-id=activeListContainer], button[aria-haspopup], [data-automation-id=searchBox]")) closePopup(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePopup(); }, true);

  // ---- widgets ------------------------------------------------------------------------------------------------
  const opts = (d) => (d.dataset.options || "").split("|").filter(Boolean);
  const BUILD = {
    text(d) {
      const id = d.dataset.inputId || nid();
      const input = h(d.dataset.multiline != null ? "textarea" : "input", { type: d.dataset.multiline != null ? null : "text", id, "data-automation-id": d.dataset.auto || null,
        "aria-required": d.dataset.required != null ? "true" : null, value: d.dataset.value || null });
      if (d.dataset.value) input.value = d.dataset.value;
      return field(d.dataset.field, label(d.dataset.label, id, d.dataset.required != null), h("div", {}, input));
    },
    listbox(d) {
      const id = nid();
      const b = h("button", { type: "button", id, "aria-haspopup": "listbox", value: "", "aria-label": `${d.dataset.label} Select One${d.dataset.required != null ? " Required" : ""}`,
        "data-automation-id": d.dataset.auto || null }, d.dataset.value || "Select One");
      b.addEventListener("click", () => popup(b, opts(d), (o) => { b.textContent = o; b.value = o; b.setAttribute("aria-label", `${d.dataset.label} ${o}`); }));
      return field(d.dataset.field, label(d.dataset.label, id, d.dataset.required != null), h("div", {}, b));
    },
    prompt(d) {
      const id = nid();
      const chosen = h("ul", { role: "listbox", "data-automation-id": "selectedItemList" });
      const input = h("input", { type: "text", id, "data-automation-id": "searchBox", placeholder: "Search", "aria-required": d.dataset.required != null ? "true" : null });
      const add = (o) => {
        if (d.dataset.single != null) chosen.replaceChildren();
        chosen.append(h("li", { "data-automation-id": "selectedItem", title: o }, o, h("button", { type: "button", "aria-label": `Delete ${o}`, "data-automation-id": "DELETE_charm" }, "×")));
        input.value = "";
      };
      for (const v of (d.dataset.value || "").split("|").filter(Boolean)) add(v);
      input.addEventListener("keydown", (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        const want = input.value.trim().toLowerCase();
        const words = want.split(/\s+/).filter(Boolean);
        const hits = opts(d).filter((o) => words.every((w) => o.toLowerCase().includes(w)));
        popup(input, hits.length ? hits : ["No Items."], (o) => { if (o !== "No Items.") add(o); }, true);
      });
      const box = h("div", { "data-automation-id": "multiSelectContainer" }, chosen, h("div", { "data-automation-id": "multiselectInputContainer" }, input));
      return field(d.dataset.field, label(d.dataset.label, id, d.dataset.required != null), box);
    },
    radio(d) {
      const name = d.dataset.field;
      const items = opts(d).map((o) => { const id = nid(); return h("div", {}, h("input", { type: "radio", id, name, value: o }), h("label", { for: id }, o)); });
      return field(d.dataset.field, h("fieldset", {}, h("legend", {}, h("label", {}, d.dataset.label, d.dataset.required != null ? h("abbr", { "aria-hidden": "true" }, "*") : null)),
        h("div", { role: "radiogroup" }, items)));
    },
    checkbox(d) {
      const id = nid();
      const box = h("input", { type: "checkbox", id, "data-automation-id": d.dataset.auto || null, "aria-checked": "false" });
      box.addEventListener("change", () => box.setAttribute("aria-checked", String(box.checked)));
      return field(d.dataset.field, d.dataset.question ? h("div", { "data-automation-id": "richText" }, h("p", {}, d.dataset.question)) : null,
        h("div", {}, box, h("label", { for: id }, d.dataset.label)));
    },
    checkboxes(d) {
      const items = opts(d).map((o) => { const id = nid(); return h("div", {}, h("input", { type: "checkbox", id, name: d.dataset.field }), h("label", { for: id }, o)); });
      return field(d.dataset.field, h("fieldset", {}, h("legend", {}, h("label", {}, d.dataset.label)), h("div", { role: "group" }, items)));
    },
    date(d) {
      const parts = (d.dataset.parts || "MM|YYYY").split("|");
      const auto = { MM: "Month", DD: "Day", YYYY: "Year" };
      const inputs = parts.map((p) => h("input", { type: "text", "data-automation-id": `dateSection${auto[p]}-input`, "aria-label": auto[p], role: "spinbutton", inputmode: "numeric" }));
      return field(d.dataset.field, h("label", {}, d.dataset.label, d.dataset.required != null ? h("abbr", {}, "*") : null),
        h("div", { "data-automation-id": "dateInputWrapper" }, inputs.map((i) => h("div", { "data-automation-id": "dateSection" }, i))));
    },
    file(d) {
      const input = h("input", { type: "file", "data-automation-id": "file-upload-input-ref", accept: ".pdf,.doc,.docx" });
      input.style.cssText = "position:absolute;width:1px;height:1px;opacity:0";
      const zone = h("div", { "data-automation-id": "file-upload-drop-zone" }, h("p", {}, "Drop files here"), h("button", { type: "button", "data-automation-id": "select-files" }, "Select files"), input);
      const wrap = field(d.dataset.field, h("label", {}, d.dataset.label), zone);
      input.addEventListener("change", () => {
        const f = input.files[0];
        if (!f) return;
        W.uploaded = { name: f.name, size: f.size, type: f.type };
        setTimeout(() => {
          zone.append(h("div", { "data-automation-id": "file-upload-successful" }, h("span", { "data-automation-id": "file-upload-item" }, f.name)));
          setTimeout(() => parse(), 500);                // Workday reads the resume into the page, badly
        }, 300);
      });
      return wrap;
    },
  };

  // ---- repeating panels (Work Experience / Education / Websites) ----------------------------------------------
  const SECTIONS = {
    work: { title: "Work Experience", auto: "workExperienceSection", panel: "workExperience", fields: (i) => [
      { kind: "text", label: "Job Title", inputId: `workExperience-${i}--jobTitle`, required: 1 },
      { kind: "text", label: "Company", inputId: `workExperience-${i}--companyName`, required: 1 },
      { kind: "text", label: "Location", inputId: `workExperience-${i}--location` },
      { kind: "checkbox", label: "I currently work here", auto: "currentlyWorkHere", field: "currentlyWorkHere" },
      { kind: "date", label: "From", field: "startDate", required: 1 },
      { kind: "date", label: "To", field: "endDate", required: 1 },
      { kind: "text", label: "Role Description", inputId: `workExperience-${i}--roleDescription`, multiline: 1 }] },
    education: { title: "Education", auto: "educationSection", panel: "education", fields: () => [
      { kind: "prompt", label: "School or University", field: "school", single: 1, required: 1,
        options: "University of Central Missouri|University of Missouri|University of Kansas|Osmania University|Other" },
      { kind: "listbox", label: "Degree", field: "degree", required: 1, options: "Associate's Degree|Bachelor's Degree|Master's Degree|Doctorate|High School Diploma" },
      { kind: "prompt", label: "Field of Study", field: "fieldOfStudy", single: 1, options: "Computer Science|Computer Engineering|Data Science|Information Technology" },
      { kind: "date", label: "From", field: "firstYearAttended", parts: "YYYY" },
      { kind: "date", label: "To (Actual or Expected)", field: "lastYearAttended", parts: "YYYY" }] },
    websites: { title: "Websites", auto: "websiteSection", panel: "websitePanelSet", fields: (i) => [
      { kind: "text", label: "URL", inputId: `websitePanelSet-${i}--url`, required: 1 }] },
  };

  function fromSpec(spec) {
    const d = document.createElement("div");
    for (const [k, v] of Object.entries(spec)) if (k !== "kind") d.dataset[k] = String(v);
    if (spec.required) d.dataset.required = "";
    if (!spec.required) delete d.dataset.required;
    if (spec.single) d.dataset.single = "";
    if (spec.multiline) d.dataset.multiline = "";
    d.dataset.field = spec.field || (spec.inputId ? spec.inputId.split("--")[1] : spec.label);
    return BUILD[spec.kind](d);
  }

  function renumber(sec) {
    const s = SECTIONS[sec.dataset.sec];
    [...sec.querySelectorAll(":scope > [role=group]")].forEach((p, n) => {
      p.setAttribute("data-automation-id", `${s.panel}-${n + 1}`);
      p.querySelector("h4").textContent = `${s.title} ${n + 1}`;
      p.setAttribute("aria-label", `${s.title} ${n + 1}`);
    });
  }

  function addPanel(sec, values = {}) {
    const s = SECTIONS[sec.dataset.sec];
    const i = sec.querySelectorAll(":scope > [role=group]").length + 1;
    const del = h("button", { type: "button", "data-automation-id": "panel-set-delete-button", "aria-label": `Delete ${s.title} ${i}` }, "Delete");
    const panel = h("div", { role: "group", "data-automation-id": `${s.panel}-${i}`, "aria-label": `${s.title} ${i}` }, h("h4", {}, `${s.title} ${i}`), del,
      s.fields(i).map(fromSpec));
    del.addEventListener("click", () => { panel.remove(); renumber(sec); });
    sec.insertBefore(panel, sec.querySelector(":scope > [data-automation-id=add-button]"));
    for (const [k, v] of Object.entries(values)) {
      const input = panel.querySelector(`[id$="--${k}"]`);
      if (input) input.value = v;
    }
    renumber(sec);
    return panel;
  }

  function section(d) {
    const s = SECTIONS[d.dataset.section];
    const sec = h("div", { "data-automation-id": s.auto, "data-sec": d.dataset.section }, h("h3", {}, s.title));
    const add = h("button", { type: "button", "data-automation-id": "add-button" }, "Add");
    add.addEventListener("click", () => { add.textContent = "Add Another"; addPanel(sec); });
    sec.append(add);
    return sec;
  }

  // the resume "parse": three work rows (one is junk, one has the wrong company), two schools, three websites (two junk)
  function parse() {
    const work = document.querySelector("[data-sec=work]");
    const edu = document.querySelector("[data-sec=education]");
    if (!work) return;
    work.querySelectorAll(":scope > [role=group]").forEach((p) => p.remove());
    addPanel(work, { jobTitle: "AI Engineer", companyName: "Examplecorp USA Inc", location: "" });
    addPanel(work, { jobTitle: "ML Engineer", companyName: "Testworks", location: "India" });
    addPanel(work, { jobTitle: "Python PyTorch RAG", companyName: "Skills", location: "" });
    edu.querySelectorAll(":scope > [role=group]").forEach((p) => p.remove());
    addPanel(edu);
    addPanel(edu);
    const web = document.querySelector("[data-sec=websites]");
    if (web) {                                           // two wrong links read off the resume
      addPanel(web, { url: "https://www.linkedin.com/in/zz-testcase-example" });
      addPanel(web, { url: "https://zz-wrong.example/parsed-junk" });
      addPanel(web, { url: "https://zz-wrong.example/more-junk" });
    }
    W.parsed = true;
  }

  // ---- the guarded buttons --------------------------------------------------------------------------------------
  // NVIDIA-style chooser: "Sign in with email" shows the email form (Workday's own script, stripped from the fixture)
  document.addEventListener("click", (e) => {
    const b = e.target instanceof Element && e.target.closest("[data-automation-id=SignInWithEmailButton]");
    if (!b) return;
    W.clicks.push({ id: "SignInWithEmailButton", trusted: e.isTrusted, text: b.textContent.trim() });
    const form = h("form", { "data-automation-id": "signInFormo" },
      field("email", label("Email Address", "input-6", true), h("div", {}, h("input", { type: "text", id: "input-6", "data-automation-id": "email" }))),
      field("password", label("Password", "input-7", true), h("div", {}, h("input", { type: "password", id: "input-7", "data-automation-id": "password" }))),
      h("button", { type: "submit", "data-automation-id": "signInSubmitButton" }, "Sign In"));
    b.closest("div").replaceWith(form);
  }, true);

  const GUARDED = /^(bottom-navigation-next-button|bottom-navigation-footer-button|createAccountSubmitButton|signInSubmitButton|click_filter|signInLink|backToJobPosting)$/;
  document.addEventListener("click", (e) => {
    const b = e.target instanceof Element && e.target.closest("[data-automation-id]");
    const id = b && b.getAttribute("data-automation-id");
    if (!id || !GUARDED.test(id)) return;
    W.clicks.push({ id, trusted: e.isTrusted, text: b.textContent.trim() });
    if (id === "signInLink" && document.querySelector("[data-automation-id=verifyPassword]")) toSignIn();
    if (!e.isTrusted) return;
    if (id === "bottom-navigation-next-button") next();
  }, true);

  function toSignIn() {                                    // Workday's Create Account form turns into Sign In
    document.querySelector("[data-automation-id=formField-verifyPassword]")?.remove();
    document.querySelector("[data-automation-id=createAccountCheckbox]")?.closest("[data-automation-id^=formField]")?.remove();
    document.querySelectorAll("[data-automation-id=errorMessage]").forEach((x) => x.remove());
    const t = document.getElementById("authViewTitle");
    if (t) t.textContent = "Sign In";
    const b = document.querySelector("[data-automation-id=createAccountSubmitButton]");
    if (b) { b.setAttribute("data-automation-id", "signInSubmitButton"); b.textContent = "Sign In"; }
    document.querySelector("[data-automation-id=click_filter]")?.setAttribute("aria-label", "Sign In");
  }

  // ---- the multi-step flow (one URL, steps swapped in like Workday's single-page app) -----------------------------
  const FLOW = ["info", "experience", "questions1", "questions2", "disclosures", "self_identify", "review"];
  const NAMES = { info: "My Information", experience: "My Experience", questions1: "Application Questions 1 of 2",
    questions2: "Application Questions 2 of 2", disclosures: "Voluntary Disclosures", self_identify: "Self Identify", review: "Review" };
  const PAGE_IDS = { info: "applyFlowMyInfoPage", experience: "applyFlowMyExpPage", questions1: "applyFlowPrimaryQuestionsPage",
    questions2: "applyFlowSecondaryQuestionsPage", disclosures: "applyFlowVoluntaryDisclosuresPage", self_identify: "applyFlowSelfIdentifyPage",
    review: "applyFlowReviewPage" };

  function expand(root) {
    for (const d of [...root.querySelectorAll("[data-wd]")]) d.replaceWith(d.dataset.wd === "section" ? section(d) : BUILD[d.dataset.wd](d));
  }

  async function show(step) {
    const flow = document.querySelector("[data-automation-id=applyFlowPage]");
    const html = await (await fetch(`/__sim/steps/${step}.html`)).text();
    const n = FLOW.indexOf(step);
    const bar = h("ol", { "data-automation-id": "progressBar" }, FLOW.map((s, i) => h("li", { "data-automation-id": i === n ? "progressBarActiveStep" : i < n ? "progressBarCompletedStep" : "progressBarInactiveStep" },
      h("label", {}, `${i === n ? "current " : ""}step ${i + 1} of ${FLOW.length}`), h("label", {}, NAMES[s]))));
    const body = h("div", { "data-automation-id": PAGE_IDS[step] });
    body.innerHTML = html;
    expand(body);
    const foot = h("div", { class: "wd-foot" }, step === "review"
      ? h("button", { type: "button", "data-automation-id": "bottom-navigation-next-button" }, "Submit")
      : h("button", { type: "button", "data-automation-id": "bottom-navigation-next-button" }, "Save and Continue"));
    flow.replaceChildren(h("h2", { "data-automation-id": "jobTitleHeading" }, "AI Engineer"), bar, body, foot);
    W.step = step;
    W.steps.push(step);
  }

  function next() {
    const n = FLOW.indexOf(W.step);
    if (W.step === "review") {
      document.querySelector("[data-automation-id=applyFlowPage]").replaceChildren(h("div", { "data-automation-id": "congratulationsPopup", role: "dialog" },
        h("h2", {}, "Congratulations!"), h("p", {}, "Your application has been submitted.")));
      W.submitted = true;
      return;
    }
    if (n >= 0) show(FLOW[n + 1]);
  }

  // ---- start -----------------------------------------------------------------------------------------------------
  const st = document.createElement("style");
  st.textContent = STYLE;
  document.head.append(st);
  const params = new URLSearchParams(location.search);
  if (params.get("exists") && document.querySelector("[data-automation-id=verifyPassword]")) {
    document.querySelector("[data-automation-id=signInFormo]")?.prepend(h("div", { "data-automation-id": "errorMessage", role: "alert" },
      "An account with this email address already exists. Sign in instead."));
  }
  if (params.get("captcha") === "box") {                  // an "I'm not a robot" box in the form
    const c = h("div", { class: "g-recaptcha" });
    c.style.cssText = "width:304px;height:78px;border:1px solid #ccc";
    (document.querySelector("form") || document.body).append(c);
  }
  if (params.get("captcha") === "challenge") {            // a picture challenge on top of the page
    const f = h("iframe", { src: "https://www.google.com/recaptcha/api2/bframe?hl=en&k=test" });
    f.style.cssText = "position:fixed;top:40px;left:40px;width:400px;height:520px;z-index:99;background:#fff;border:1px solid #888";
    document.body.append(f);
  }
  W.ready = Promise.resolve(params.get("step") ? show(params.get("step")) : null).then(() => { W.loaded = true; });
  W.show = show;
})();

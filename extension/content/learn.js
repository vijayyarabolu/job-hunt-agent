// Job Hunt Agent Autofill, content script: what the user fills by hand. Every field's value is remembered when it is
// first seen and when Autofill fills it; a field the user touches (a real click, key or pick, or an edit in the panel) and
// leaves with a different answer is a lesson: "hand" (the user answered it), "override" (the user changed Autofill's answer) or
// "panel" (the user set it from the panel). The batch goes to the service worker as the user works and to Job Hunt Agent when the user leaves
// the step, so a closed tab loses nothing. Never: files, password boxes, secret / ID / bank / birth-date fields, or
// any value holding the job-site password.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const MAX_CONTROLS = 12;
  const MAX_VALUE = 4000;
  const CONTAINER = "[data-automation-id^=formField-], .field, .form-group, fieldset, .application-question, [class*=question i]";
  const CONTROLS = "input, select, textarea, button[aria-haspopup]";
  const SECRET = /pass ?(word|code|phrase)|\bpin\b|\bssn\b|social security|tax (id|identification)|\bitin\b|national (id|identity|insurance)|passport|driver'?s? licen[cs]e|licen[cs]e (number|no)|\bid (number|no)\b|identification number|\bbank\b|routing|account (number|no)|\biban\b|\bswift\b|credit card|card number|\bcv[vc]\b|security code|date of birth|birth ?date|\bdob\b|maiden name|security question/i;
  const KIND = { combo: "select", listbox: "select" };
  const touched = new WeakSet();
  const trace = new Map();                         // field id -> { base, ours, panel, options }
  let lastTrigger = null;

  const entry = (id) => { if (!trace.has(id)) trace.set(id, {}); return trace.get(id); };
  const show = (f) => (f.kind === "checkbox" ? (f.value ? "Yes" : "") : String(f.value || "").replace(/\s+/g, " ").trim());

  function touch(target) {
    if (!(target instanceof Element) || target.closest("[data-jh-panel]")) return;
    const option = target.closest("[role=option], [data-automation-id=promptOption], .select__option");
    if (option && lastTrigger) {                    // a pick in a dropdown's list: the list's choices go with it
      touched.add(lastTrigger);
      const list = option.closest("[role=listbox], .select__menu, [class*=-menu]") || option.parentElement;
      const texts = [...list.querySelectorAll("[role=option], [data-automation-id=promptOption], .select__option")].map(JH.text).filter(Boolean);
      if (lastTrigger.dataset.jhId && texts.length) entry(lastTrigger.dataset.jhId).options = texts.slice(0, 80);
      return;
    }
    const own = target.closest(CONTROLS);
    const box = target.closest(CONTAINER);
    const inside = box ? [...box.querySelectorAll(CONTROLS)] : [];
    for (const c of inside.length && inside.length <= MAX_CONTROLS ? inside : own ? [own] : []) touched.add(c);
    if (own && (own.matches("button[aria-haspopup]") || own.getAttribute("role") === "combobox" || own.getAttribute("aria-autocomplete"))) lastTrigger = own;
  }
  for (const type of ["pointerdown", "keydown", "input", "change"]) {
    document.addEventListener(type, (e) => { if (JH.realEvent(e)) touch(e.target); }, true);
  }

  const isMine = (f) => touched.has(f.el) || (f.els || []).some((e) => touched.has(e)) || Boolean(entry(f.id).panel) || JH.byHand(f.el);

  JH.learn = {
    // a field's value before the user or Autofill changed it (one the user already touched started empty)
    see(fields) {
      for (const f of fields) {
        const t = entry(f.id);
        if (t.base === undefined) t.base = isMine(f) ? "" : show(f);
      }
    },
    // what Autofill put in (after a fill): changing it later is an override
    filled(fields) {
      for (const f of fields) if ([f.el, ...(f.els || [])].some((e) => e.hasAttribute("data-jh-filled")) && show(f)) entry(f.id).ours = show(f);
    },
    panelSet(id) { entry(id).panel = true; },
    // GOAL30 §4: saved by the panel where the user chose (this job / company / everywhere): never learned a second time
    explicit(id) { entry(id).explicit = true; entry(id).panel = true; },
    mine: isMine,
    status(f) {                                     // the panel's group for one field
      const value = show(f);
      const mark = JH.marks ? JH.marks.get(f.el) : null;   // GOAL27: the engine's result for this field
      if (!value) return mark === "needs" || mark === "failed" || f.el.closest("[data-jh-need]") ? "needs" : "skipped";
      const t = entry(f.id);
      return t.panel || (isMine(f) && value !== (t.ours ?? null)) ? "yours" : mark === "review" ? "review" : mark === "failed" ? "needs" : "filled";
    },
    // this step's lessons: { items, hand } (hand = fields the user filled themselves, for Stats)
    batch(fields, b, password = "") {
      this.see(fields);
      const items = [];
      let hand = 0;
      for (const f of fields) {
        if (f.kind === "file" || !isMine(f)) continue;
        if (f.el && (f.el.type === "search" || f.el.getAttribute("role") === "searchbox")) continue;   // a site's search box
        const t = entry(f.id);
        const value = show(f);
        if (t.explicit) continue;                                                // saved already, where the user chose
        if (!value || (!t.panel && value === (t.base ?? ""))) continue;          // unchanged
        if (t.ours != null && value === t.ours && !t.panel) continue;            // Autofill's answer, only clicked
        if (t.ours == null) hand++;
        if (SECRET.test(`${f.label} ${(f.hints || []).join(" ")}`)) continue;
        if (password.length >= 4 && value.includes(password)) continue;
        try { if (b && b.noLearn && b.noLearn(f.el)) continue; } catch { continue; }
        items.push({ label: f.label, kind: KIND[f.kind] || f.kind, value: value.slice(0, MAX_VALUE),
                     options: (t.options || f.options || []).slice(0, 80), hints: f.hints || [], section: f.section || "",
                     how: t.panel ? "panel" : t.ours != null ? "override" : "hand" });
      }
      return { items, hand };
    },
  };
})();

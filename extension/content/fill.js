// Job Hunt Agent Autofill, content script 4/n: put one answer into one field and check it took. A field the person typed
// into is kept; a field that already holds something is kept unless the board asks to correct it (Workday's parsed
// resume). Dropdowns are opened, the choice is picked by meaning (JH.pick) and clicked; when no choice fits, the
// field is "Needs you" with the choices it offered, never a guess.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const OPTION_SEL = "[role=option], [data-automation-id=promptOption], [data-automation-id=menuItem], .select__option, [class*=-option]";
  const result = (status, extra = {}) => ({ status, ...extra });
  const DATE_BOX = /mm\s*\/\s*dd|dd\s*\/\s*mm|yyyy/i;
  const DATE_VALUE = /^\d{1,4}[\/.-]\d{1,4}([\/.-]\d{1,4})?$/;          // 06/15/2026, 2026-06-15, 03/2023

  function visibleOptions(scope = document) {
    return JH.all(OPTION_SEL, scope).filter((o) => JH.visible(o) && !o.closest("[data-jh-panel]") && JH.text(o));
  }

  function openList(trigger) {
    const id = trigger.getAttribute("aria-controls") || trigger.getAttribute("aria-owns");
    const own = id && document.getElementById(id);
    if (own && JH.visible(own)) return own;
    const lists = JH.all("[role=listbox], .select__menu, [class*=-menu], [data-automation-id=activeListContainer]").filter((l) => JH.visible(l) && !l.closest("[data-jh-panel]"));
    return lists[lists.length - 1] || null;
  }

  // a city typeahead lists every "Springfield": the one in the address's state and the United States (user 2026-10-14)
  const US = /\b(united states( of america)?|usa|us)\b/i;
  function bestPlace(texts, place) {
    const word = (w) => new RegExp(`(^|[^a-z])${w.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}([^a-z]|$)`, "i");
    const city = word(place.city);
    let top = null;
    texts.forEach((t, index) => {
      if (!city.test(t)) return;
      const score = (place.state && word(place.state).test(t) ? 2 : 0) + (place.code && word(place.code).test(t) ? 2 : 0) + (US.test(t) ? 1 : 0)
        + (JH.pick.norm(t).startsWith(JH.pick.norm(place.city)) ? 0.5 : 0);
      if (!top || score > top.score) top = { index, text: t, score };
    });
    return top;
  }

  async function chooseFrom(trigger, answers, { search = null, place = null } = {}) {
    // the list is open (or opening): pick the option that means the answer and click it
    const list = await JH.waitFor(() => { const l = openList(trigger); return l && visibleOptions(l).length ? l : null; }, 2500);
    const options = list ? visibleOptions(list) : visibleOptions();
    const texts = options.map((o) => JH.text(o));
    const hit = (place && bestPlace(texts, place)) || JH.pick.best(texts, answers);
    if (!hit) return { ok: false, options: texts };
    const target = options[hit.index];
    target.scrollIntoView({ block: "nearest" });
    JH.click(target);
    await JH.sleep(120);
    return { ok: true, text: hit.text, target, options: texts, search };
  }

  function closeLists(trigger) {
    JH.key(document.activeElement || trigger, "Escape");
  }

  async function fillListbox(f, answers) {
    const btn = f.el;
    if (btn.getAttribute("aria-expanded") !== "true") JH.click(btn);
    const got = await chooseFrom(btn, answers);
    if (!got.ok) { closeLists(btn); return result("needs", { options: got.options, why: "no choice fits the saved answer" }); }
    const took = await JH.waitFor(() => JH.pick.norm(JH.text(btn)).includes(JH.pick.norm(got.text).slice(0, 20)), 800);
    if (!took && got.target) {
      if (!(await JH.react(got.target, "click"))) {
        JH.click(btn);                                    // re-open, click once more
        const again = await chooseFrom(btn, [got.text]);
        if (!again.ok) closeLists(btn);
      }
    }
    JH.mark(btn);
    return result("filled", { text: got.text });
  }

  async function fillCombo(f, answers, place = null) {
    const input = f.el;
    const box = input.closest(".select__control, [class*=-control]") || input;
    JH.click(box);
    const query = String(place ? place.city : answers[answers.length - 1]).slice(0, 40);
    await JH.type(input, query);
    let got = await chooseFrom(input, answers, { search: query, place });
    if (!got.ok && query.split(" ").length > 1) {        // a long answer finds nothing: search its first word
      await JH.type(input, query.split(" ")[0]);
      got = await chooseFrom(input, answers);
    }
    if (!got.ok) {
      JH.key(input, "Escape");
      await JH.type(input, "");
      return result("needs", { options: got.options, why: "no choice fits the saved answer" });
    }
    JH.mark(input);
    return result("filled", { text: got.text });
  }

  function fillSelect(f, answers) {
    const options = [...f.el.options];
    const hit = JH.pick.best(options.map((o) => JH.text(o)), answers);
    if (!hit) return result("needs", { options: f.options, why: "no choice fits the saved answer" });
    JH.setValue(f.el, options[hit.index].value);
    return result("filled", { text: hit.text });
  }

  const ticked = (input) => input.checked || (input.closest("[role=radio], [role=checkbox]") || {}).ariaChecked === "true";

  async function fillChoices(f, answers, many) {
    const labels = f.els.map((i) => JH.labelOf(i));
    const wanted = many ? String(answers[answers.length - 1]).split(/\s*;\s*/) : [null];
    let done = 0;
    for (const want of wanted) {
      const hit = JH.pick.best(labels, many ? [want] : answers);
      if (!hit) continue;
      const input = f.els[hit.index];
      if (!ticked(input)) {
        const label = (input.id && document.querySelector(`label[for="${CSS.escape(input.id)}"]`)) || input.closest("label");
        JH.click(JH.visible(input) ? input : label || input);
        await JH.waitFor(() => ticked(input), 800);          // React boards tick it on their next render
      }
      if (ticked(input)) { JH.mark(input); done++; }
    }
    return done ? result("filled") : result("needs", { options: labels, why: "no choice fits the saved answer" });
  }

  async function fillCheckbox(f, answer) {
    if (!JH.pick.truthy(answer)) return result("skipped", { why: "saved answer is no" });
    const input = f.el;
    if (!ticked(input)) {
      const label = (input.id && document.querySelector(`label[for="${CSS.escape(input.id)}"]`)) || input.closest("label");
      JH.click(JH.visible(input) ? input : label || input);
      await JH.waitFor(() => ticked(input), 800);
    }
    JH.mark(input);
    return ticked(input) ? result("filled") : result("needs", { why: "the box did not take the tick" });
  }

  function dateValue(el, answer) {
    const m = /^(\d{1,2})\/(?:(\d{1,2})\/)?(\d{4})$/.exec(String(answer).trim());
    if (!m) return String(answer);
    const [mm, dd, yyyy] = [m[1].padStart(2, "0"), (m[2] || "01").padStart(2, "0"), m[3]];
    return el.type === "month" ? `${yyyy}-${mm}` : `${yyyy}-${mm}-${dd}`;
  }

  // a date written the way the box asks: dd/mm/yyyy, yyyy-mm-dd, mm/yyyy, else mm/dd/yyyy
  function dateText(hint, d) {
    if (/dd\s*\/\s*mm/i.test(hint)) return `${d.dd}/${d.mm}/${d.yyyy}`;
    if (/yyyy\s*-\s*mm/i.test(hint)) return `${d.yyyy}-${d.mm}-${d.dd}`;
    if (/mm\s*\/\s*yyyy/i.test(hint) && !/dd/i.test(hint)) return `${d.mm}/${d.yyyy}`;
    return `${d.mm}/${d.dd}/${d.yyyy}`;
  }

  function textValue(el, answer) {
    if (el.type === "number") return String(answer).replace(/[^\d.]/g, "");
    if (el.type === "tel" || /phone/i.test(el.name || el.id || "")) return String(answer);
    return String(answer);
  }

  // the choices a dropdown offers, for "Needs you" (opened, read, closed; nothing picked)
  async function readOptions(f) {
    if (f.kind !== "listbox" && f.kind !== "combo") return f.options || [];
    const trigger = f.kind === "combo" ? (f.el.closest(".select__control, [class*=-control]") || f.el) : f.el;
    if (f.el.getAttribute("aria-expanded") !== "true") JH.click(trigger);
    const list = await JH.waitFor(() => { const l = openList(f.el); return l && visibleOptions(l).length ? l : null; }, 1500);
    const texts = list ? visibleOptions(list).map((o) => JH.text(o)).slice(0, 80) : [];
    closeLists(f.el);
    await JH.sleep(80);
    if (openList(f.el)) JH.key(f.el, "Escape");
    return texts.filter((t) => !/^no items\.?$/i.test(t));
  }

  // one skill into a typeahead: type it, wait for the list, pick the entry that is this skill (or the
  // site's own "Create / Add <skill>" entry when it takes free text). {ok} or {ok: false} (skipped, listed);
  // skip(text): the entry is already in the box -> nothing is clicked ({ok, already}).
  async function skill(input, want, { enter = false, skip = null } = {}) {
    await JH.type(input, String(want).slice(0, 60));
    if (enter) JH.key(input, "Enter");                   // Workday searches on Enter
    const list = await JH.waitFor(() => { const l = openList(input); return l && visibleOptions(l).length ? l : null; }, 2500);
    const options = list ? visibleOptions(list) : [];
    const texts = options.map((o) => JH.text(o));
    let i = JH.pick.skill(texts, want);
    if (i < 0) i = texts.findIndex((t) => JH.pick.creates(t, want));
    if (i < 0) { JH.key(input, "Escape"); await JH.type(input, ""); return { ok: false }; }
    if (skip && skip(texts[i])) { JH.key(input, "Escape"); await JH.type(input, ""); return { ok: true, text: texts[i], already: true }; }
    options[i].scrollIntoView({ block: "nearest" });
    JH.click(options[i]);
    await JH.sleep(150);
    JH.key(input, "Escape");
    return { ok: true, text: texts[i] };
  }

  const busy = new WeakSet();                       // fields being filled now

  JH.fill = {
    // one filler per field at a time: a board step that timed out may still be running when the plain filler starts
    async field(f, ans, opts) {
      return JH.fill.once(f.el, () => JH.fill.fillOne(f, ans, opts), result("kept", { why: "it is being filled" }));
    },
    // fn() unless another filler is working on el right now (then busyResult): also for a board's own widgets
    async once(el, fn, busyResult) {
      if (!el || busy.has(el)) return busyResult;
      busy.add(el);
      try { return await fn(); } finally { busy.delete(el); }
    },
    // f: a field from JH.fields.collect; ans: Job Hunt Agent's answer {value, option}; overwrite: correct a pre-filled value
    async fillOne(f, ans, { overwrite = false, force = false } = {}) {
      if (!force && (f.hand || JH.byHand(f.el))) return result("kept", { why: "you filled it" });   // force: the user's own edit in the panel
      const answers = [ans.option, ans.value].filter((a) => a != null && String(a).trim() !== "");
      if (!answers.length) return result("needs");
      const current = String(f.value || "").trim();
      if (current && f.kind !== "checkbox") {
        if (JH.pick.best([current], answers)) return result("filled", { already: true });
        if (!overwrite) return result("kept", { why: "it already had an answer" });
      }
      f.el.scrollIntoView({ block: "center", behavior: "instant" });
      switch (f.kind) {
        case "text": case "textarea":
          if (DATE_BOX.test(`${f.el.placeholder} ${f.label}`) && !DATE_VALUE.test(String(ans.value).trim())) {
            const ends = JH.pick.waitDate(ans.value);       // "2 weeks after offer acceptance": today + 2 weeks
            if (!ends) return result("needs", { why: "a date box: the saved answer is not a date" });
            JH.setValue(f.el, dateText(`${f.el.placeholder} ${f.label}`, ends));
            return result("filled");
          }
          JH.setValue(f.el, textValue(f.el, ans.value));
          return result("filled");
        case "date": {
          const ends = DATE_VALUE.test(String(ans.value).trim()) ? null : JH.pick.waitDate(ans.value);
          if (!ends && !DATE_VALUE.test(String(ans.value).trim())) return result("needs", { why: "a date box: the saved answer is not a date" });
          JH.setValue(f.el, dateValue(f.el, ends ? `${ends.mm}/${ends.dd}/${ends.yyyy}` : ans.value));
          return result("filled");
        }
        case "select": return fillSelect(f, answers);
        case "radio": return fillChoices(f, answers, false);
        case "checkboxes": return fillChoices(f, answers, true);
        case "checkbox": return fillCheckbox(f, ans.value);
        case "combo": return fillCombo(f, answers, ans.place || null);
        case "listbox": return fillListbox(f, answers);
        default: return result("skipped");
      }
    },
    chooseFrom, openList, visibleOptions, closeLists, readOptions, skill,
  };
})();

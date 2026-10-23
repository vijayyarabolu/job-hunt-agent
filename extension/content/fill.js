// Job Hunt Agent Autofill 1.3, the widget layer (GOAL27 §3-§4): put one answer into one control the way a person would and
// check that it stuck. Text: the element's own value setter + input / change / blur (React, Angular, Vue), and when
// the page drops it, typed key by key, then the page's own React handler. Native selects, select2 / chosen (through
// the page's jQuery), React-select / Greenhouse / Ashby / Workday dropdowns (opened, async options awaited, the
// choice picked by meaning and clicked, the shown pick checked), radios, checkboxes, Yes / No button groups, dates
// (date / month inputs, MM/DD/YYYY boxes, waits like "2 weeks"), phone numbers in the box's own shape. A field the user
// typed into is kept; a field holding something is kept unless the board asks to correct it. Nothing fits -> "Needs
// you" with the choices it offered, never a guess.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const OPTION_SEL = "[role=option], [data-automation-id=promptOption], [data-automation-id=menuItem], .select__option, [class*=-option], .select2-results__option, .chosen-results li.active-result";
  const result = (status, extra = {}) => ({ status, ...extra });
  const DATE_BOX = /mm\s*\/\s*dd|dd\s*\/\s*mm|yyyy/i;
  const DATE_VALUE = /^\d{1,4}[\/.-]\d{1,4}([\/.-]\d{1,4})?$/;          // 06/15/2026, 2026-06-15, 03/2023
  const STUCK_MS = 120;
  const CLEAR_WAIT_MS = 1500;                 // GOAL29 §4: how long a typeahead may take to clear its last pick
  const RETYPES = 2;                          // ... and how often a skill is retyped when the box does not read it
  const LONG_LIST = 50;                       // a longer list may show only part of itself: searching it is allowed
  const OPEN_TRIES = 3;                       // GOAL31: how often a dropdown is opened before it counts as not opening

  // 1.3.1: what is already picked (Workday's selectedItem rows, chips) is never a list of choices
  const PICKED = "[data-automation-id=selectedItemList], [data-automation-id=selectedItem], [class*=multi-value], [class*=multiValue]";
  const optionText = (o) => (o.getAttribute("data-automation-label") || (o.querySelector("[data-automation-label]") || {}).dataset?.automationLabel || JH.text(o)).trim();
  function visibleOptions(scope = document) {
    const all = JH.all(OPTION_SEL, scope).filter((o) => JH.visible(o) && !o.closest("[data-jh-panel]") && !o.closest(PICKED) && JH.text(o) &&
      o.getAttribute("aria-disabled") !== "true" && !/^(loading|searching)\b/i.test(JH.text(o)));
    return all.filter((o) => !all.some((x) => x !== o && o.contains(x) && optionText(x) === optionText(o)));   // a row and its own label: once
  }

  // GOAL30 §0b (NVIDIA, 2026-10-20): Workday leaves the last dropdown's list on screen while the next one opens, so
  // "the last visible list" was the previous question's choices (Veteran Status read as races, "none of its choices
  // means No"). A list counts only when it opened after this click: new, or showing other choices than before.
  const LISTS = "[role=listbox], .select__menu, [class*=-menu], [data-automation-id=activeListContainer], .select2-results, .chosen-drop";
  function snapshot() {
    const out = new Map();
    for (const l of JH.all(LISTS)) if (JH.visible(l) && !l.closest("[data-jh-panel]") && !l.matches(PICKED) && !l.closest(PICKED)) out.set(l, visibleOptions(l).map(optionText).join("|"));
    return out;
  }
  const fresh = (l, before) => !before || !before.has(l) || before.get(l) !== visibleOptions(l).map(optionText).join("|");
  async function closeOpen(trigger) {               // a list still open from the last question goes first
    if (!snapshot().size) return;
    JH.key(document.activeElement || trigger, "Escape");
    await JH.waitFor(() => !snapshot().size, 500, 50);
  }

  function openList(trigger) {
    const id = trigger.getAttribute("aria-controls") || trigger.getAttribute("aria-owns");
    const own = id && document.getElementById(id);
    if (own && JH.visible(own) && !own.matches(PICKED)) return own;
    const lists = JH.all("[role=listbox], .select__menu, [class*=-menu], [data-automation-id=activeListContainer], .select2-results, .chosen-drop")
      .filter((l) => JH.visible(l) && !l.closest("[data-jh-panel]") && !l.matches(PICKED) && !l.closest(PICKED));
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

  async function chooseFrom(trigger, answers, { search = null, place = null, wait = 3000, before = null } = {}) {
    // the list is open (or opening; options may load from the network): pick the option that means the answer
    const list = await JH.waitFor(() => { const l = openList(trigger); return l && visibleOptions(l).length && fresh(l, before) ? l : null; }, wait);
    if (!list && before) return { ok: false, options: [] };              // never a list left from another question
    const options = list ? visibleOptions(list) : visibleOptions();
    const texts = options.map(optionText);
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

  const shows = (el, text) => JH.pick.norm(JH.text(el)).includes(JH.pick.norm(text).slice(0, 20));

  // GOAL31 (NVIDIA and 4 other tenants, 2026-10-20: "none of its choices means Male" with Male in its list): a list
  // may open empty (its choices still loading), not open at all (the page still waking up), or show the last
  // question's choices arriving late. Opened up to 3 times; never "no choice fits" on an empty or unopened list, and
  // a list where nothing fits counts only when a second open shows the same choices.
  async function openAndChoose(btn, answers) {
    let got = { ok: false, options: [] };
    let seen = null;
    for (let i = 0; i < OPEN_TRIES; i++) {
      await closeOpen(btn);
      const before = snapshot();
      if (btn.getAttribute("aria-expanded") !== "true") JH.click(btn);
      got = await chooseFrom(btn, answers, { before });
      if (got.ok) return got;
      closeLists(btn);
      const now = got.options.join("|");
      if (now && now === seen) return got;
      if (now) seen = now;
    }
    return got;
  }

  async function fillListbox(f, answers) {
    const btn = f.el;
    const got = await openAndChoose(btn, answers);
    if (!got.ok && !got.options.length) return result("needs", { options: [], why: `its list did not open (tried ${OPEN_TRIES} times): pick it yourself` });
    if (!got.ok) return result("needs", { options: got.options, why: "no choice fits the saved answer" });
    let took = await JH.waitFor(() => shows(btn, got.text), 800);
    if (!took && got.target) {                            // retry: the page's own handler, else open and click again
      if (!(await JH.react(got.target, "click"))) {
        const prev = snapshot();
        JH.click(btn);
        const again = await chooseFrom(btn, [got.text], { before: prev });
        if (!again.ok) closeLists(btn);
      }
      took = await JH.waitFor(() => shows(btn, got.text), 800);
    }
    JH.mark(btn);
    return took ? result("filled", { text: got.text }) : result("failed", { why: `picked “${got.text}” but the page did not show it` });
  }

  // the shown pick of a React-select / Greenhouse / Ashby combobox
  function comboShown(input) {
    const box = input.closest(".select__control, [class*=-control]") || input.closest("[class*=container]") || input.parentElement;
    const chosen = box && box.querySelector(".select__single-value, [class*=singleValue], [class*=multiValue], [class*=single-value]");
    return chosen ? JH.text(chosen) : input.value;
  }

  async function fillCombo(f, answers, place = null) {
    const input = f.el;
    const box = input.closest(".select__control, [class*=-control]") || input;
    await closeOpen(input);
    const before = snapshot();
    JH.click(box);
    const query = String(place ? place.city : answers[answers.length - 1]).slice(0, 40);
    // a short list opens on click: pick from it before typing (Greenhouse's Yes / No questions)
    let got = { ok: false, options: [] };
    const open = await JH.waitFor(() => { const l = openList(input); return l && visibleOptions(l).length && fresh(l, before) ? l : null; }, 700);
    if (open && !place) {
      got = await chooseFrom(input, answers, { wait: 300, before });
      // 1.3.1: a list that opened with its choices is read, never typed into: nothing fits -> the user's question
      if (!got.ok && got.options.length && got.options.length <= LONG_LIST) {
        JH.key(input, "Escape");
        return result("needs", { options: got.options, why: "no choice fits the saved answer" });
      }
    }
    if (!got.ok) {
      await JH.type(input, query);
      got = await chooseFrom(input, answers, { search: query, place });
    }
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
    const took = await JH.waitFor(() => JH.pick.norm(comboShown(input)).includes(JH.pick.norm(got.text).slice(0, 12)) || !JH.visible(input), 900);
    return took || comboShown(input) ? result("filled", { text: got.text }) : result("failed", { why: `picked “${got.text}” but the page did not show it` });
  }

  // a <select>; hidden behind select2 / chosen it is told through the page's jQuery too
  async function fillSelect(f, answers) {
    const el = f.el;
    const options = [...el.options];
    const hit = JH.pick.best(options.map((o) => JH.text(o)), answers);
    if (!hit) return result("needs", { options: f.options, why: "no choice fits the saved answer" });
    const value = options[hit.index].value;
    JH.setValue(el, value);
    if (f.proxy || !JH.visible(el)) await JH.react(el, "jq", value);
    await JH.sleep(STUCK_MS);
    if (el.value !== value) {
      el.selectedIndex = hit.index;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      await JH.react(el, "change", value);
    }
    return el.value === value ? result("filled", { text: hit.text }) : result("failed", { why: "the page changed the choice back" });
  }

  const ticked = (input) => input.checked || (input.closest("[role=radio], [role=checkbox]") || {}).ariaChecked === "true";
  const labelFor = (input) => (input.id && document.querySelector(`label[for="${CSS.escape(input.id)}"]`)) || input.closest("label");

  async function tick(input) {
    if (ticked(input)) return true;
    const label = labelFor(input);
    JH.click(JH.visible(input) ? input : label || input);
    if (await JH.waitFor(() => ticked(input), 800)) return true;      // React boards tick it on their next render
    if (label && JH.visible(label)) JH.click(label);
    return Boolean(await JH.waitFor(() => ticked(input), 600));
  }

  async function fillChoices(f, answers, many) {
    const labels = f.els.map((i) => JH.labelOf(i));
    const wanted = many ? String(answers[answers.length - 1]).split(/\s*;\s*/) : [null];
    let done = 0, missed = 0, text = "";
    for (const want of wanted) {
      const hit = JH.pick.best(labels, many ? [want] : answers);
      if (!hit) continue;
      const input = f.els[hit.index];
      if (await tick(input)) { JH.mark(input); done++; text = hit.text; } else missed++;
    }
    if (done) return result("filled", { text });
    return missed ? result("failed", { why: "the choice did not take the click" }) : result("needs", { options: labels, why: "no choice fits the saved answer" });
  }

  async function fillCheckbox(f, answer) {
    if (!JH.pick.truthy(answer)) return result("skipped", { why: "saved answer is no" });
    const ok = await tick(f.el);
    JH.mark(f.el);
    return ok ? result("filled") : result("failed", { why: "the box did not take the tick" });
  }

  // GOAL27: Yes / No (or any few) buttons standing for one question (Ashby, Dover, Wellfound, Rippling)
  const pressed = (b) => b.getAttribute("aria-pressed") === "true" || b.getAttribute("aria-checked") === "true" ||
    b.getAttribute("data-state") === "on" || /\b(selected|active|checked|is-selected|isSelected|_active)\b/.test(b.className || "") ||
    HASHED_PRESSED.test(b.className || "") || Boolean(b.querySelector && b.querySelector("input[type=radio]:checked, input[type=checkbox]:checked"));
  // GOAL30 §0c: a CSS module's hashed name ("_active_1svni_47": Ashby marks the chosen one that way); a choice holding
  // a ticked radio (Workable's role=radio cards: the box inside is the truth before the page redraws the card)
  const HASHED_PRESSED = /(^|\s)_?(active|selected|checked|pressed)_[A-Za-z0-9]+_\d+(\s|$)/;
  async function fillButtons(f, answers) {
    const texts = f.els.map((b) => JH.text(b));
    const hit = JH.pick.best(texts, answers);
    if (!hit) return result("needs", { options: texts, why: "no choice fits the saved answer" });
    const b = f.els[hit.index];
    if (!pressed(b)) {
      JH.click(b);
      if (!(await JH.waitFor(() => pressed(b), 700))) await JH.react(b, "click");
      const box = !pressed(b) && b.querySelector && b.querySelector("input[type=radio], input[type=checkbox]");
      if (box) JH.click(box);                           // GOAL30 §0c: a card around its own radio (Workable)
    }
    JH.mark(b);
    return (await JH.waitFor(() => pressed(b), 500)) ? result("filled", { text: hit.text }) : result("filled", { text: hit.text, why: "clicked; the page shows no pressed state" });
  }

  function dateValue(el, answer) {
    const m = /^(\d{1,2})\/(?:(\d{1,2})\/)?(\d{4})$/.exec(String(answer).trim());
    if (!m) return String(answer);
    const [mm, dd, yyyy] = [m[1].padStart(2, "0"), (m[2] || "01").padStart(2, "0"), m[3]];
    return el.type === "month" ? `${yyyy}-${mm}` : `${yyyy}-${mm}-${dd}`;
  }

  // GOAL30 §1.5: the date an answer means: a start date (or notice) question is today + the user's "N weeks" setting; a date
  // saved as one ("06/2026", "06/15/2026", "2026-06-15") is itself; a wait ("2 weeks after offer acceptance") ends
  // today + the wait. {mm, dd, yyyy} or null.
  function dateOf(ans) {
    if (ans.weeks != null && /^(start_date|notice)$/.test(ans.key || "")) return JH.pick.waitDate(`${Number(ans.weeks)} weeks`);
    const raw = String(ans.value == null ? "" : ans.value).trim();
    let m = /^(\d{1,2})\/(?:(\d{1,2})\/)?(\d{4})$/.exec(raw);
    if (m) return { mm: m[1].padStart(2, "0"), dd: (m[2] || "01").padStart(2, "0"), yyyy: m[3] };
    m = /^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?$/.exec(raw);
    if (m) return { mm: m[2].padStart(2, "0"), dd: (m[3] || "01").padStart(2, "0"), yyyy: m[1] };
    return JH.pick.waitDate(raw);
  }
  // ... as Workday's parts want it: "MM/DD/YYYY" for three parts (or any other box), else "MM/YYYY"
  function dateFor(f, ans) {
    const d = dateOf(ans);
    if (!d) return null;
    return f.parts && f.parts < 3 ? `${d.mm}/${d.yyyy}` : `${d.mm}/${d.dd}/${d.yyyy}`;
  }

  // a date written the way the box asks: dd/mm/yyyy, yyyy-mm-dd, mm/yyyy, else mm/dd/yyyy
  function dateText(hint, d) {
    if (/dd\s*\/\s*mm/i.test(hint)) return `${d.dd}/${d.mm}/${d.yyyy}`;
    if (/yyyy\s*-\s*mm/i.test(hint)) return `${d.yyyy}-${d.mm}-${d.dd}`;
    if (/mm\s*\/\s*yyyy/i.test(hint) && !/dd/i.test(hint)) return `${d.mm}/${d.yyyy}`;
    return `${d.mm}/${d.dd}/${d.yyyy}`;
  }

  // a phone number in the box's own shape: digits only for a short box or an international widget that already
  // carries the country code (+1 is not typed twice), else as saved
  function phoneValue(el, answer) {
    const raw = String(answer).trim();
    let digits = raw.replace(/\D/g, "");
    const intl = el.closest(".iti, .intl-tel-input, .react-tel-input, [class*=PhoneInput], [class*=phone-input]");
    if (digits.length === 11 && digits.startsWith("1") && (intl || /^\+?1[\s.-]/.test(raw))) digits = digits.slice(1);
    const max = el.maxLength > 0 ? el.maxLength : 0;
    if (intl || (max && max < raw.length) || /^\d+$/.test(el.placeholder || "") || el.inputMode === "numeric") return digits;
    if (/^\(\d{3}\) ?\d{3}-\d{4}$/.test(el.placeholder || "") && digits.length === 10) return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`;
    return raw;
  }

  function textValue(f, answer) {
    const el = f.el;
    if (el.type === "number") return String(answer).replace(/[^\d.]/g, "");
    if (el.type === "tel" || (f.cls && f.cls.key === "phone")) return phoneValue(el, answer);
    if (el.maxLength > 0 && String(answer).length > el.maxLength) return String(answer).slice(0, el.maxLength);
    return String(answer);
  }

  const same = (a, b) => String(a).replace(/\s+/g, " ").trim() === String(b).replace(/\s+/g, " ").trim() ||
    (/\d/.test(a) && String(a).replace(/\D/g, "") === String(b).replace(/\D/g, "") && String(b).replace(/\D/g, "").length >= 7);

  // GOAL27 §3: set, check it stuck, retry typed key by key, then through the page's React handler
  async function putText(el, value) {
    JH.setValue(el, value);
    await JH.sleep(STUCK_MS);
    if (same(el.value, value)) return true;
    await JH.type(el, value);
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.dispatchEvent(new FocusEvent("blur", { bubbles: false }));
    await JH.sleep(STUCK_MS);
    if (same(el.value, value)) return true;
    await JH.react(el, "change", value);
    await JH.sleep(STUCK_MS);
    return same(el.value, value);
  }

  // the choices a dropdown offers, for "Needs you" (opened, read, closed; nothing picked)
  async function readOptions(f) {
    if (f.kind === "buttons") return f.els.map((b) => JH.text(b));
    if (f.kind !== "listbox" && f.kind !== "combo") return f.options || [];
    const trigger = f.kind === "combo" ? (f.el.closest(".select__control, [class*=-control]") || f.el) : f.el;
    await closeOpen(f.el);
    const before = snapshot();
    if (f.el.getAttribute("aria-expanded") !== "true") JH.click(trigger);
    const list = await JH.waitFor(() => { const l = openList(f.el); return l && visibleOptions(l).length && fresh(l, before) ? l : null; }, 1500);
    const texts = list ? visibleOptions(list).map(optionText).slice(0, 80) : [];
    if (texts.length && JH.fields.note) JH.fields.note(f.el, { options: texts });   // GOAL30 §1.3: the panel's chips
    closeLists(f.el);
    await JH.sleep(80);
    if (openList(f.el)) JH.key(f.el, "Escape");
    return texts.filter((t) => !/^no (items|options|results)\.?$/i.test(t));
  }

  // one skill into a typeahead: type it, wait for the list, pick the entry that is this skill (or the
  // site's own "Create / Add <skill>" entry when it takes free text). {ok} or {ok: false} (skipped, listed);
  // skip(text): the entry is already in the box -> nothing is clicked ({ok, already}).
  // 1.3.1: type -> wait for the results (Workday answers after "Searching…") -> the entry that is this skill ->
  // clicked (its own handler when a plain click does not take); the caller waits for the pick to show
  // GOAL29 §4 (IRC's Workday, 2026-10-19): the next skill was typed before Workday cleared the last pick, the clear
  // landed mid-typing and Enter saved "hain" for LangChain. Now: an empty, focused box first (cleared by us when the
  // site never does), then type, then the box must read the skill exactly (retyped up to RETYPES times) before Enter.
  async function emptyBox(input) {
    input.focus({ preventScroll: true });
    const ready = () => input.value === "" && document.activeElement === input;
    if (!(await JH.waitFor(ready, CLEAR_WAIT_MS, 40))) await JH.type(input, "");
    await JH.sleep(STUCK_MS);
    return ready();
  }
  async function typeExactly(input, text) {
    for (let n = 0; n <= RETYPES; n++) {
      if (!(await emptyBox(input))) continue;
      await JH.type(input, text);
      await JH.sleep(STUCK_MS);
      if (input.value === text) return true;
    }
    return false;
  }
  // taken(): the site already saved a pick (a tenant that keeps free text on Enter): no list to wait for
  async function skill(input, want, { enter = false, skip = null, taken = null } = {}) {
    const text = String(want).slice(0, 60);
    if (!(await typeExactly(input, text))) { JH.key(input, "Escape"); await JH.type(input, ""); return { ok: false, unclean: true }; }
    if (enter) JH.key(input, "Enter");                   // Workday searches on Enter
    const results = () => { const l = openList(input); const o = l ? visibleOptions(l) : []; return o.length && !l.querySelector("[data-automation-id=promptSearchingIndicator]") ? o : null; };
    const options = (await JH.waitFor(() => (taken && taken() ? "taken" : results()), 6000)) || [];
    if (options === "taken") { closeLists(input); return { ok: true, text, typed: true }; }
    const texts = options.map(optionText);
    let i = JH.pick.skill(texts, want);
    if (i < 0) i = texts.findIndex((t) => JH.pick.creates(t, want));
    if (i < 0) { JH.key(input, "Escape"); await JH.type(input, ""); return { ok: false }; }
    if (skip && skip(texts[i])) { JH.key(input, "Escape"); await JH.type(input, ""); return { ok: true, text: texts[i], already: true }; }
    const target = options[i].querySelector("[data-automation-id=promptOption]") || options[i];
    target.scrollIntoView({ block: "nearest" });
    JH.click(target);
    await JH.sleep(200);
    if (target.isConnected && openList(input)) { await JH.react(target, "click"); await JH.sleep(150); }   // still open: its own handler
    JH.key(input, "Escape");
    return { ok: true, text: texts[i] };
  }

  // what the field shows now, as a person reads it ("" when nothing is picked)
  function valueNow(f) {
    const el = f.el;
    if (f.kind === "select") { const o = el.options[el.selectedIndex]; return o && el.value ? JH.text(o) : ""; }
    if (f.kind === "radio" || f.kind === "checkboxes") return f.els.filter(ticked).map((i) => JH.labelOf(i)).join("; ");
    if (f.kind === "checkbox") return ticked(el) ? "checked" : "";
    if (f.kind === "buttons") return JH.text(f.els.find(pressed) || { innerText: "" });
    if (f.kind === "listbox") { const t = JH.text(el); return JH.fields.PLACEHOLDER_VALUE.test(t) ? "" : t; }
    if (f.kind === "combo") return comboShown(el);
    return el.value || "";
  }
  // the field shows the user's answer: the option just picked for it ("No" for "I am not a protected veteran"), or the same words
  const holds = (f, answers, picked = "") => {
    const v = valueNow(f);
    if (f.kind === "checkbox") return Boolean(v) === JH.pick.truthy(answers[answers.length - 1]);
    return Boolean(v) && Boolean((picked && same(v, picked)) || JH.pick.best([v], answers) || answers.some((a) => same(v, a)));
  };

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
        if (JH.pick.best([current], answers) || same(current, ans.value)) return result("filled", { already: true });
        // 1.3.1: a legal / EEO answer is always the user's saved one: a pre-filled one that disagrees is corrected
        if (!overwrite && !ans.legal) return result("kept", { why: "it already had an answer" });
      }
      f.el.scrollIntoView({ block: "center", behavior: "instant" });
      if (!ans.legal || f.kind === "text" || f.kind === "textarea" || f.kind === "date") return JH.fill.put(f, ans, answers);
      // legal / EEO: checked after filling, set once more when the page shows something else
      let got = await JH.fill.put(f, ans, answers);
      if (got.status === "filled" && !holds(f, answers, got.text)) { await JH.sleep(250); got = await JH.fill.put(f, ans, answers); }
      if (got.status === "filled" && !holds(f, answers, got.text)) return result("failed", { why: `the form shows “${valueNow(f) || "nothing"}”, not your answer` });
      return got.status === "filled" && current && f.kind !== "checkbox" ? { ...got, why: `corrected: the form had “${current}”` } : got;
    },
    // the widget's own way in (no checks of what was there before)
    async put(f, ans, answers) {
      switch (f.kind) {
        case "text": case "textarea": {
          let value = textValue(f, ans.value);
          if (DATE_BOX.test(`${f.el.placeholder} ${f.label}`) && (!DATE_VALUE.test(String(ans.value).trim()) || ans.weeks != null)) {
            const ends = dateOf(ans);                       // "2 weeks after offer acceptance" / the user's N weeks: from today
            if (!ends) return result("needs", { why: "a date box: the saved answer is not a date" });
            value = dateText(`${f.el.placeholder} ${f.label}`, ends);
          } else if (ans.weeks != null && f.kind === "text" && /^(start_date|notice)$/.test(ans.key || "") && !JH.pick.wait(ans.value)) {
            value = `${Number(ans.weeks)} weeks`;           // a plain box asking when: the user's wait, in words
          }
          return (await putText(f.el, value)) ? result("filled") : result("failed", { why: "the page did not keep the text" });
        }
        case "date": {
          const ends = dateOf(ans);
          if (!ends) return result("needs", { why: "a date box: the saved answer is not a date" });
          const value = dateValue(f.el, `${ends.mm}/${ends.dd}/${ends.yyyy}`);
          return (await putText(f.el, value)) ? result("filled") : result("failed", { why: "the date box did not keep the date" });
        }
        case "select": return fillSelect(f, answers);
        case "radio": return fillChoices(f, answers, false);
        case "checkboxes": return fillChoices(f, answers, true);
        case "checkbox": return fillCheckbox(f, ans.value);
        case "combo": return fillCombo(f, answers, ans.place || null);
        case "listbox": return fillListbox(f, answers);
        case "buttons": return fillButtons(f, answers);
        default: return result("skipped");
      }
    },
    chooseFrom, openList, visibleOptions, closeLists, readOptions, skill, putText, phoneValue, pressed, optionText, valueNow,
    dateFor, dateOf, snapshot, closeOpen,
  };
})();

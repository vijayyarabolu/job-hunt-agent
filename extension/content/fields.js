// Job Hunt Agent Autofill, content script 3/n: the page's questions. Every fillable control (inputs, text areas, selects,
// radio and checkbox groups, React / Workday dropdowns, typeaheads, file inputs) as one field: its question label,
// its choices, whether it is required, the section it is in and what it holds now. Honeypots ("for robots only"),
// hidden and disabled controls, password boxes and the panel itself are left out.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const TEXT_TYPES = /^(text|email|tel|url|number|search|)$/;
  const SKIP_TYPES = /^(hidden|submit|button|reset|image|password)$/;
  const LABELISH = "label, legend, [class*=label i], [class*=question i], [class*=title i], [data-automation-id=richText], h3, h4, h5, p, span, div";
  const PLACEHOLDER_VALUE = /^(select( one| an option)?|please select|choose( one)?|--+|none selected|search|start typing.*)$/i;
  let counter = 0;
  // GOAL30: what the engine learned about a field during a fill (the choices it read off a dropdown, the key Job Hunt Agent
  // gave it, the saved answer's origin), kept for the panel's rows
  const seen = new WeakMap();
  const note = (el, patch) => { if (el) seen.set(el, { ...(seen.get(el) || {}), ...patch }); };

  const clean = (s) => String(s || "").replace(/\s+/g, " ").replace(/\s*\*\s*$/, "").replace(/\(required\)/ig, "").trim();
  const byId = (id, el) => (id ? (el.getRootNode().getElementById ? el.getRootNode().getElementById(id) : null) || document.getElementById(id) : null);

  function ownText(node, skip) {
    // the text of a label without the text of the control inside it (a select's options, an input's value)
    const copy = node.cloneNode(true);
    for (const bad of copy.querySelectorAll("select, option, input, textarea, button[aria-haspopup], ul[role=listbox], [aria-hidden=true], script, style")) bad.remove();
    if (skip) for (const bad of copy.querySelectorAll(skip)) bad.remove();
    return clean(copy.textContent);
  }

  function labelledBy(el) {
    const ids = (el.getAttribute("aria-labelledby") || "").split(/\s+/).filter(Boolean);
    return clean(ids.map((id) => JH.text(byId(id, el))).join(" "));
  }

  function nearbyText(el, stopAt) {
    // the question text written just before the control inside its form row (no <label for>)
    let node = el;
    for (let depth = 0; depth < 5 && node && node !== stopAt; depth++) {
      const parent = node.parentElement;
      if (!parent) break;
      for (let sib = node.previousElementSibling; sib; sib = sib.previousElementSibling) {
        if (sib.matches("input, select, textarea, button")) continue;
        const text = ownText(sib);
        if (text && text.length < 400) return text;
      }
      node = parent;
      const lab = [...parent.children].find((c) => c !== node && c.matches && c.matches("label, legend") && !c.contains(el));
      if (lab) return ownText(lab);
    }
    return "";
  }

  JH.labelOf = function labelOf(el) {
    const aria = labelledBy(el);
    if (aria) return aria;
    if (el.id) {
      const lab = el.getRootNode().querySelector ? el.getRootNode().querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
      if (lab && ownText(lab)) return ownText(lab);
    }
    const wrap = el.closest("label");
    if (wrap && ownText(wrap)) return ownText(wrap);
    const field = el.closest("[data-automation-id^=formField-], .field, .form-group, .application-question, [class*=question i], [class*=field i]");
    const own = clean(el.getAttribute("aria-label")) || clean(el.placeholder);
    if (field) {
      const lab = [...field.querySelectorAll("label, legend, [data-automation-id=richText]")].find((l) => !l.contains(el) && ownText(l));
      const other = lab && lab.htmlFor && lab.htmlFor !== el.id && byId(lab.htmlFor, el);   // "Address" is the street box's,
      if (lab && !(other && own)) return ownText(lab);                                      // City / State say their own
      if (other) return own;
    }
    return clean(el.getAttribute("aria-label")) || nearbyText(el) || clean(el.placeholder) || clean(el.title) || clean(el.name);
  };

  function groupLabel(inputs) {
    const first = inputs[0];
    const set = first.closest("fieldset, [role=radiogroup], [role=group]");
    const optionLabels = new Set(inputs.map((i) => JH.labelOf(i)));
    if (set) {
      const legend = set.querySelector("legend");
      const text = labelledBy(set) || (legend && ownText(legend)) || clean(set.getAttribute("aria-label"));
      if (text) return text;
      // GOAL31: Ashby's survey asks in a <label> inside the fieldset ("Gender"), its choices in labels of their own
      const own = [...set.querySelectorAll("label")].find((l) => !inputs.some((i) => l.contains(i) || l.htmlFor === i.id) &&
        ownText(l) && !optionLabels.has(ownText(l)));
      if (own) return ownText(own);
    }
    const field = first.closest("[data-automation-id^=formField-], .field, .form-group, .application-question, [class*=question i]");
    if (field) {
      const lab = [...field.querySelectorAll(LABELISH)].find((l) => !inputs.some((i) => l.contains(i)) && ownText(l) && !optionLabels.has(ownText(l)));
      if (lab) return ownText(lab);
    }
    // GOAL30 §0c: from the box holding all the choices, so a choice's own label ("No - I do not consent to receiving
    // text messages") is never the question (Ashby's texting consent: the words before the Yes / No)
    const near = nearbyText(set || around(inputs));
    return optionLabels.has(near) ? "" : near;
  }

  // the smallest element holding every one of the inputs
  function around(inputs) {
    let box = inputs[0].parentElement;
    while (box && !inputs.every((i) => box.contains(i))) box = box.parentElement;
    return box || inputs[0].parentElement;
  }

  function required(el, label) {
    if (el.required || el.getAttribute("aria-required") === "true") return true;
    const field = el.closest("[data-automation-id^=formField-], .field, .form-group, fieldset, [class*=question i]");
    if (field && field.querySelector("abbr[title*=required i], .required, [class*=required i], [aria-required=true]")) return true;
    return /\*\s*$|\(required\)/i.test(label || "") || /\*\s*$/.test(field ? JH.text(field.querySelector("label, legend")) : "");
  }

  function hints(el) {
    const field = el.closest("[data-automation-id^=formField-]");
    const out = [el.name, el.id, el.getAttribute("autocomplete"), el.getAttribute("data-automation-id"), el.getAttribute("data-qa"),
                 el.getAttribute("data-testid"), field && field.getAttribute("data-automation-id").replace(/^formField-/, "")];
    return [...new Set(out.filter((h) => h && h !== "on" && h !== "off" && !/^input-\d+$/.test(h)).map((h) => String(h).toLowerCase()))].slice(0, 8);
  }

  // the heading of the part of the form the control sits in: the nearest heading before it in the page (a
  // fieldset's legend only for its own controls), skipping headings that are themselves a question ("Select all ...")
  const HEADING = "h1, h2, h3, h4, h5, legend, [role=heading], [data-automation-id$=Heading], [class*=section-title i], [class*=sectionTitle], [class*=section-header i]";
  function section(el) {
    const host = el.closest("[data-automation-id$=Page], form") || document.body;
    let heading = null;
    for (const h of host.querySelectorAll(HEADING)) {
      if (!(h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) || h.closest("[data-jh-panel]")) continue;
      if (h.tagName === "LEGEND" && !h.parentElement.contains(el)) continue;
      const t = JH.text(h);
      if (!t || t.length > 80 || /\?\s*\*?$|^(select|please|are you|do you|have you|will you)\b/i.test(t)) continue;
      heading = h;
    }
    // a repeater entry's own title ("Work Experience 2") counts first
    // GOAL30 §0: also a panel named only by aria-labelledby ("Work Experience 1" in a plain div, NVIDIA's Workday); a
    // date's own small group inside it ("From") is passed over for the entry around it
    const SEL = "[role=group][aria-label], [role=group][aria-labelledby], [data-automation-id^=workExperience-], [data-automation-id^=education-]";
    const ENTRY = /^(work experience|employment|experience|job|position|education|school|degree) \d{1,2}$/i;
    const nameOf = (g) => {
      const by = clean((g.getAttribute("aria-labelledby") || "").split(/\s+/).map((id) => JH.text(byId(id, el))).join(" "));
      const head = JH.text(g.querySelector("h3, h4, h5, legend"));
      return g.getAttribute("aria-label") || (ENTRY.test(by) ? by : "") || (ENTRY.test(head) ? head : "") ||
        JH.text([...g.querySelectorAll("div, span, p, strong")].slice(0, 12).find((n) => ENTRY.test(JH.text(n)))) ||
        (g.matches("[data-automation-id^=workExperience-], [data-automation-id^=education-]") ? head : "");
    };
    let own = "";
    for (let g = el.closest(SEL); g && !own; g = g.parentElement ? g.parentElement.closest(SEL) : null) own = nameOf(g);
    return String(own || (heading ? JH.text(heading) : "")).slice(0, 120);
  }

  const FRAME = Math.random().toString(36).slice(2, 6);   // ids unique across a tab's frames (panel list)
  function idOf(el) {
    if (!el.dataset.jhId) el.dataset.jhId = `f${FRAME}${++counter}`;
    return el.dataset.jhId;
  }

  // "RaceHispanic or Latino A person of …": a question whose label runs on into its choices' descriptions
  const PICK_TEXT = /(please )?(select|choose)( an?| one| your)?( \w+)?$|^--+$/i;
  function trimmed(label, options, value) {
    const text = clean(label);
    // "CountryUnited States", "StateFL", "StateSelect a state": a label glued to the dropdown's shown pick
    for (const o of [value, ...(options || [])].map(clean).filter((x) => x.length >= 2)) {
      const at = text.length - o.length;
      if (at >= 3 && text.endsWith(o) && /[a-z)?:]/i.test(text[at - 1]) && text[at - 1] !== " ") return text.slice(0, at).trim();
    }
    const glued = text.match(/^(.{3,60}?[a-z?:)])((please )?(select|choose)\b.*)$/);
    if (glued && PICK_TEXT.test(glued[2])) return glued[1].trim();
    if (text.length < 80 || !options || !options.length) return text;
    const cut = options.map((o) => clean(o)).filter((o) => o.length > 3).map((o) => text.indexOf(o)).filter((i) => i > 0);
    return cut.length ? text.slice(0, Math.min(...cut)).trim() : text;
  }

  // GOAL34 §3: a file box read under its whole card ("Cover letter OptionalUpload your cover letterFile types
  // supported ...") is named by the card's first line, its heading ("Cover letter")
  const FILE_LABEL_MAX = 40;
  const OPTIONAL = /\s*(optional|required)$/i;
  const blockOf = (node) => {
    for (let e = node.parentElement; e; e = e.parentElement) if (!/^inline/.test(getComputedStyle(e).display)) return e;
    return node.parentElement;
  };
  function fileLabel(el, label) {
    if (label.length <= FILE_LABEL_MAX) return label;
    for (let node = el.parentElement, depth = 0; node && depth < 5; node = node.parentElement, depth++) {
      if (!ownText(node).includes(label)) continue;
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT, { acceptNode: (t) =>
        (clean(t.nodeValue) && !t.parentElement.closest("script, style, [aria-hidden=true]") ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP) });
      const first = walker.nextNode();
      const head = first ? ownText(blockOf(first)).replace(OPTIONAL, "") : "";
      return head.length >= 3 && head.length < label.length && label.startsWith(head) ? head : label;
    }
    return label;
  }

  function base(el, kind, label, extra = {}) {
    const f = { id: idOf(el), el, kind, label: trimmed(label, extra.options, extra.value).slice(0, 500), hints: hints(el), required: required(el, label),
                section: section(el), hand: JH.byHand(el), placeholder: el.getAttribute("placeholder") || "", type: (el.getAttribute("type") || "").toLowerCase(),
                autocomplete: el.getAttribute("autocomplete") || "", name: el.getAttribute("name") || "", ...extra };
    f.cls = JH.classify ? JH.classify.field(f) : { key: null };       // GOAL27: the canonical field it asks for
    const known = seen.get(el);
    if (known) {
      if (known.options && !(f.options && f.options.length)) f.options = known.options;
      if (known.key && !f.cls.key) f.cls = { ...f.cls, key: known.key, server: true };
      if (known.origin) f.origin = known.origin;
      if (known.saved) f.saved = true;                  // GOAL36 §5.4: filled from the user's saved answer
      if (known.flag) f.flag = known.flag;
    }
    return f;
  }

  const usable = (el) => !el.disabled && !el.closest("[data-jh-panel]") && JH.shown(el);
  const comboInput = (el) => el.getAttribute("role") === "combobox" || el.getAttribute("aria-autocomplete") === "list" ||
    Boolean(el.closest(".select__control, [class*=-control] [class*=ValueContainer], [data-automation-id=multiselectInputContainer]"));

  function listboxValue(btn) {
    const text = JH.text(btn);
    return PLACEHOLDER_VALUE.test(text) ? "" : text;
  }

  function comboValue(input) {
    const box = input.closest(".select__control, [class*=-control], [data-automation-id=multiSelectContainer]") || input.parentElement;
    const chosen = box && box.querySelector(".select__single-value, [class*=singleValue], [class*=multiValue], [data-automation-id=selectedItem]");
    return chosen ? JH.text(chosen).replace(/\s*[×✕]\s*$/, "") : input.value;   // a pick's own delete "×" is not its value
  }

  // GOAL27: a <select> hidden behind select2 / chosen: the visible box standing for it
  function dressed(el) {
    if (JH.shown(el) || el.closest("[data-jh-panel]")) return null;
    const next = el.nextElementSibling;
    const box = next && next.matches(".select2-container, .chosen-container, .select2") ? next
      : el.id ? document.getElementById(`s2id_${el.id}`) || document.getElementById(`${el.id}_chosen`) : null;
    return box && JH.visible(box) ? box : null;
  }

  // GOAL27: Yes / No buttons (or a few short choices) that stand for one question: 2-6 plain buttons side by side,
  // in a radio group or saying Yes and No
  const BUTTON_TEXT = /^.{1,40}$/;
  function buttonGroups(root) {
    const byParent = new Map();
    for (const b of JH.all("button:not([aria-haspopup]):not([type=submit]), [role=radio][tabindex], [role=button][aria-pressed]", root)) {
      if (b.closest("[data-jh-panel], nav, header, footer, [role=listbox], [role=menu], [role=tablist]") || !JH.visible(b) || b.disabled) continue;
      const p = b.parentElement;
      if (!byParent.has(p)) byParent.set(p, []);
      byParent.get(p).push(b);
    }
    const out = [];
    for (const [p, set] of byParent) {
      if (set.length < 2 || set.length > 6) continue;
      const texts = set.map((b) => JH.text(b));
      if (!texts.every((t) => BUTTON_TEXT.test(t)) || texts.some((t) => JH.isForbidden && /^(next|submit|apply|back|cancel|save|upload|add|remove|delete)\b/i.test(t))) continue;
      const yesNo = texts.some((t) => JH.pick.polarity(t) === "yes") && texts.some((t) => JH.pick.polarity(t) === "no");
      if (yesNo || p.matches("[role=radiogroup], [role=group]") || set.every((b) => b.hasAttribute("aria-pressed") || b.getAttribute("role") === "radio")) out.push(set);
    }
    return out;
  }
  function buttonsLabel(set) {
    const p = set[0].parentElement;
    const own = labelledBy(p) || clean(p.getAttribute("aria-label"));
    if (own) return own;
    const field = p.closest("fieldset, .field, .form-group, .application-question, [class*=question i], [class*=field i]");
    if (field) {
      const legend = field.querySelector("legend, label");
      if (legend && !set.some((b) => legend.contains(b)) && ownText(legend)) return ownText(legend);
    }
    return nearbyText(p);
  }

  JH.fields = {
    collect(root = document) {
      const out = [];
      const groups = new Map();
      const wdDates = new Map();                         // GOAL30: a Workday date box -> its first part
      for (const el of JH.all("input, select, textarea, button[aria-haspopup=listbox]", root)) {
        const proxy = el.tagName === "SELECT" && !el.disabled ? dressed(el) : null;   // select2 / chosen show it
        if (!proxy && !usable(el)) continue;
        const type = el.tagName === "INPUT" ? el.type : (el.getAttribute("type") || "").toLowerCase();   // an unknown type is a text box
        if (el.tagName === "INPUT" && SKIP_TYPES.test(type)) continue;
        if (el.tagName === "BUTTON") {
          out.push(base(el, "listbox", JH.labelOf(el), { value: listboxValue(el) }));
          continue;
        }
        const label = el.type === "radio" || el.type === "checkbox" ? "" : JH.labelOf(el);
        if (!proxy && JH.isHoneypot(el, label)) continue;   // select2 hides its <select> the way a honeypot is hidden
        if (el.tagName === "SELECT") {
          const options = [...el.options].map((o) => clean(o.textContent)).filter(Boolean);
          const chosen = el.selectedIndex >= 0 ? clean(el.options[el.selectedIndex].textContent) : "";
          out.push(base(el, "select", label || (proxy && JH.labelOf(proxy)) || "", { options, value: el.value && !PLACEHOLDER_VALUE.test(chosen) ? chosen : "", proxy }));
        } else if (el.tagName === "TEXTAREA") {
          out.push(base(el, "textarea", label, { value: el.value }));
        } else if (type === "file") {
          out.push(base(el, "file", fileLabel(el, label) || "Resume", { value: el.files && el.files.length ? el.files[0].name : "" }));
        } else if (type === "radio" || type === "checkbox") {
          const key = `${type}:${el.name || (el.closest("fieldset, [role=radiogroup], [role=group], [data-automation-id^=formField-]") || {}).id || idOf(el)}`;
          if (!groups.has(key)) groups.set(key, []);
          groups.get(key).push(el);
        } else if (type === "date" || type === "month") {
          out.push(base(el, "date", label, { value: el.value }));
        } else if (TEXT_TYPES.test(type) && !el.readOnly) {
          if (el.closest("[data-automation-id^=dateSection], [data-automation-id=dateInputWrapper]")) {   // Workday's Month / Day / Year
            const box = el.closest("[data-automation-id^=formField-]") || el.closest("[data-automation-id=dateInputWrapper]");
            if (box && !wdDates.has(box)) wdDates.set(box, el);
            continue;
          }
          out.push(base(el, comboInput(el) ? "combo" : "text", label, { value: comboInput(el) ? comboValue(el) : el.value }));
        }
      }
      for (const [box, first] of wdDates) {             // one date field per Workday date box (MM/DD/YYYY or MM/YYYY)
        const parts = [...box.querySelectorAll("input")].filter((i) => i.closest("[data-automation-id=dateInputWrapper], [data-automation-id^=dateSection]"));
        const label = JH.text(box.querySelector("label, legend")).replace(/\*\s*$/, "").trim() || JH.labelOf(first);
        const value = parts.every((i) => i.value) ? parts.map((i) => i.value).join("/") : "";
        out.push(base(first, "date", label, { value, wdDate: box, parts: parts.length }));
      }
      for (const [key, inputs] of groups) {
        const type = key.split(":")[0];
        const options = inputs.map((i) => JH.labelOf(i));
        if (type === "checkbox" && inputs.length === 1) {
          const own = options[0];
          if (JH.isHoneypot(inputs[0], own)) continue;                  // "Leave this box empty (robots only)"
          // its question is the box's own heading, never the field before it ("End Year" + "I currently work here")
          const field = inputs[0].closest("fieldset, [role=group], [data-automation-id^=formField-], .application-question, [class*=question i]");
          const question = field ? groupLabel(inputs) : "";
          const label = question && own && !own.includes(question) && question.length < 200 ? `${question} ${own}` : own || question;
          out.push(base(inputs[0], "checkbox", label, { els: inputs, value: inputs[0].checked ? "checked" : "" }));
          continue;
        }
        const label = groupLabel(inputs);
        if (inputs.some((i) => JH.isHoneypot(i, label))) continue;
        const value = inputs.filter((i) => i.checked).map((i) => JH.labelOf(i)).join("; ");
        out.push(base(inputs[0], type === "radio" ? "radio" : "checkboxes", label, { els: inputs, options, value }));
      }
      for (const set of buttonGroups(root)) {
        const options = set.map((b) => JH.text(b));
        const label = buttonsLabel(set);
        if (!label) continue;
        out.push(base(set[0], "buttons", label, { els: set, options, value: options[set.findIndex((b) => JH.fill && JH.fill.pressed(b))] || "" }));
      }
      return out.sort((a, b) => (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
    },
    // what Job Hunt Agent needs to answer a field (no element, no current value)
    describe(f) {
      return { id: f.id, kind: f.kind === "combo" || f.kind === "listbox" || f.kind === "buttons" ? "select" : f.kind, label: f.label, hints: f.hints,
               required: f.required, options: (f.options || []).slice(0, 80), section: String(f.section || "").slice(0, 120),
               key: (f.cls && f.cls.key && !f.cls.group) ? f.cls.key : null,
               group: (f.cls && f.cls.group) || null };                    // GOAL30 §0: inside a job / school entry
    },
    PLACEHOLDER_VALUE,
    note,                                                // GOAL30: remember options / key / origin for an element
    noted: (el) => seen.get(el) || {},
  };
})();

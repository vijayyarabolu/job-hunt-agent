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
    if (set) {
      const legend = set.querySelector("legend");
      const text = labelledBy(set) || (legend && ownText(legend)) || clean(set.getAttribute("aria-label"));
      if (text) return text;
    }
    const field = first.closest("[data-automation-id^=formField-], .field, .form-group, .application-question, [class*=question i]");
    if (field) {
      const optionLabels = new Set(inputs.map((i) => JH.labelOf(i)));
      const lab = [...field.querySelectorAll(LABELISH)].find((l) => !inputs.some((i) => l.contains(i)) && ownText(l) && !optionLabels.has(ownText(l)));
      if (lab) return ownText(lab);
    }
    return nearbyText(set || first.parentElement);
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

  function section(el) {
    const host = el.closest("[data-automation-id$=Page], section, fieldset, form") || document.body;
    let heading = null;
    for (const h of host.querySelectorAll("h1, h2, h3, h4, legend, [role=heading]")) {
      if (h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) heading = h;
    }
    return heading ? JH.text(heading).slice(0, 120) : "";
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

  function base(el, kind, label, extra = {}) {
    return { id: idOf(el), el, kind, label: trimmed(label, extra.options, extra.value).slice(0, 500), hints: hints(el), required: required(el, label),
             section: section(el), hand: JH.byHand(el), ...extra };
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
    return chosen ? JH.text(chosen) : input.value;
  }

  JH.fields = {
    collect(root = document) {
      const out = [];
      const groups = new Map();
      for (const el of JH.all("input, select, textarea, button[aria-haspopup=listbox]", root)) {
        if (!usable(el)) continue;
        const type = (el.getAttribute("type") || "").toLowerCase();
        if (el.tagName === "INPUT" && SKIP_TYPES.test(type)) continue;
        if (el.tagName === "BUTTON") {
          out.push(base(el, "listbox", JH.labelOf(el), { value: listboxValue(el) }));
          continue;
        }
        const label = el.type === "radio" || el.type === "checkbox" ? "" : JH.labelOf(el);
        if (JH.isHoneypot(el, label)) continue;
        if (el.tagName === "SELECT") {
          const options = [...el.options].map((o) => clean(o.textContent)).filter(Boolean);
          const chosen = el.selectedIndex >= 0 ? clean(el.options[el.selectedIndex].textContent) : "";
          out.push(base(el, "select", label, { options, value: el.value && !PLACEHOLDER_VALUE.test(chosen) ? chosen : "" }));
        } else if (el.tagName === "TEXTAREA") {
          out.push(base(el, "textarea", label, { value: el.value }));
        } else if (type === "file") {
          out.push(base(el, "file", label || "Resume", { value: el.files && el.files.length ? el.files[0].name : "" }));
        } else if (type === "radio" || type === "checkbox") {
          const key = `${type}:${el.name || (el.closest("fieldset, [role=radiogroup], [role=group], [data-automation-id^=formField-]") || {}).id || idOf(el)}`;
          if (!groups.has(key)) groups.set(key, []);
          groups.get(key).push(el);
        } else if (type === "date" || type === "month") {
          out.push(base(el, "date", label, { value: el.value }));
        } else if (TEXT_TYPES.test(type) && !el.readOnly) {
          if (el.closest("[data-automation-id^=dateSection], [data-automation-id=dateInputWrapper]")) continue;   // Workday date parts: board code
          out.push(base(el, comboInput(el) ? "combo" : "text", label, { value: comboInput(el) ? comboValue(el) : el.value }));
        }
      }
      for (const [key, inputs] of groups) {
        const type = key.split(":")[0];
        const options = inputs.map((i) => JH.labelOf(i));
        if (type === "checkbox" && inputs.length === 1) {
          const own = options[0];
          const question = groupLabel(inputs);
          const label = question && own && !own.includes(question) && question.length < 200 ? `${question} ${own}` : own || question;
          out.push(base(inputs[0], "checkbox", label, { els: inputs, value: inputs[0].checked ? "checked" : "" }));
          continue;
        }
        const label = groupLabel(inputs);
        if (inputs.some((i) => JH.isHoneypot(i, label))) continue;
        const value = inputs.filter((i) => i.checked).map((i) => JH.labelOf(i)).join("; ");
        out.push(base(inputs[0], type === "radio" ? "radio" : "checkboxes", label, { els: inputs, options, value }));
      }
      return out.sort((a, b) => (a.el.compareDocumentPosition(b.el) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
    },
    // what Job Hunt Agent needs to answer a field (no element, no current value)
    describe(f) {
      return { id: f.id, kind: f.kind === "combo" || f.kind === "listbox" ? "select" : f.kind, label: f.label, hints: f.hints,
               required: f.required, options: (f.options || []).slice(0, 80), section: String(f.section || "").slice(0, 120) };
    },
    PLACEHOLDER_VALUE,
  };
})();

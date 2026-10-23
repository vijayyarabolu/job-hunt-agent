// GOAL27 test fixture: the interactive widgets of the major ATS apply forms, in plain JS (our own code, written from how
// the widgets behave; no ATS code). A fixture page declares a widget as <div data-w="kind" ...>; this builds it:
//   rs        a React-select style combobox (Greenhouse, Ashby, Lever EEO): .select__control, a menu that opens on
//             click, filters as you type, optional async options ("Loading…" first), the pick shown as single-value
//   typeahead a location / school search: options appear after typing 2+ letters (async), a click fills the box
//   yesno     Yes / No buttons (Ashby, Dover, Wellfound): aria-pressed and an _active class on the chosen one
//   select2   a hidden <select> dressed by a select2 / chosen style box that shows its pick on "change"
//   iti       an international phone box: flag (+1) and a tel input
//   react     a controlled text box: a value set without an input event is put back on blur (React's behaviour)
//   repeater  "Add another" entries (work history / education) cloned from a template, ids and names indexed
// Every element carrying data-expect is checked by the test: window.__simValue(el) is what a person would see in it.
// Submit / Next only act on a trusted click; every click on them is logged in window.__sim.clicks.
"use strict";
(() => {
  const S = (window.__sim = window.__sim || { clicks: [], ready: false });
  const h = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) if (v != null && v !== false) n.setAttribute(k, v === true ? "" : v);
    for (const kid of kids.flat()) if (kid != null) n.append(kid.nodeType ? kid : String(kid));
    return n;
  };
  const opts = (d) => (d.dataset.options || "").split("|").filter(Boolean);
  let seq = 0;

  // ---- React-select style combobox -------------------------------------------------------------------------------
  function rs(d) {
    const id = d.dataset.id || `rs${++seq}`;
    const lab = h("label", { id: `${id}-label`, for: id, class: "label" }, d.dataset.label, d.dataset.required != null ? h("span", { class: "required" }, " *") : null);
    const input = h("input", { id, role: "combobox", "aria-expanded": "false", "aria-autocomplete": "list", "aria-labelledby": `${id}-label`,
      class: "select__input", autocomplete: "off", type: "text", "aria-required": d.dataset.required != null ? "true" : null });
    const shown = h("div", { class: "select__placeholder" }, "Select...");
    const control = h("div", { class: "select__control" }, h("div", { class: "select__value-container" }, shown, h("div", { class: "select__input-container" }, input)),
      h("div", { class: "select__indicators" }, "▾"));
    const wrap = h("div", { class: "select__container" }, control);
    let menu = null, value = "";
    const close = () => { if (menu) menu.remove(); menu = null; input.setAttribute("aria-expanded", "false"); };
    const draw = (loading) => {
      if (!menu) { menu = h("div", { class: "select__menu" }); wrap.append(menu); }
      const q = input.value.toLowerCase();
      const list = h("div", { class: "select__menu-list", role: "listbox", id: `${id}-listbox` });
      if (loading) list.append(h("div", { class: "select__menu-notice--loading" }, "Loading..."));
      else {
        const hits = opts(d).filter((o) => !q || o.toLowerCase().includes(q));
        if (!hits.length) list.append(h("div", { class: "select__menu-notice--no-options" }, "No options"));
        for (const o of hits) {
          const opt = h("div", { class: "select__option", role: "option", "aria-selected": String(o === value), tabindex: "-1" }, o);
          opt.addEventListener("click", () => { value = o; shown.className = "select__single-value"; shown.textContent = o; input.value = ""; close(); });
          list.append(opt);
        }
      }
      menu.replaceChildren(list);
      input.setAttribute("aria-expanded", "true");
      input.setAttribute("aria-controls", `${id}-listbox`);
    };
    const open = () => {
      if (d.dataset.async != null && !menu) { draw(true); setTimeout(() => menu && draw(false), 400); } else draw(false);
    };
    control.addEventListener("mousedown", (e) => { if (e.target.closest(".select__option")) return; menu ? null : open(); });
    control.addEventListener("click", () => { if (!menu) open(); });
    input.addEventListener("input", () => { if (d.dataset.async != null) { draw(true); setTimeout(() => menu && draw(false), 300); } else draw(false); });
    input.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
    document.addEventListener("mousedown", (e) => { if (!wrap.contains(e.target)) close(); });
    const box = h("div", { class: d.dataset.field || "field" }, lab, wrap);
    box.__simValue = () => value;
    return box;
  }

  // ---- typeahead (location / school search) ------------------------------------------------------------------------
  function typeahead(d) {
    const id = d.dataset.id || `ta${++seq}`;
    const input = h("input", { id, type: "text", role: "combobox", "aria-autocomplete": "list", "aria-expanded": "false", autocomplete: "off",
      name: d.dataset.name || null, placeholder: d.dataset.placeholder || "Start typing..." });
    const wrap = h("div", { class: "typeahead" }, input);
    let list = null;
    const close = () => { if (list) list.remove(); list = null; };
    input.addEventListener("input", () => {
      close();
      const q = input.value.trim().toLowerCase();
      if (q.length < 2) return;
      setTimeout(() => {
        if (input.value.trim().toLowerCase() !== q) return;
        close();
        list = h("ul", { role: "listbox", class: "typeahead-results" });
        for (const o of opts(d).filter((x) => x.toLowerCase().includes(q.split(" ")[0]))) {
          const li = h("li", { role: "option" }, o);
          li.addEventListener("click", () => { input.value = o; input.dataset.picked = o; close(); });
          list.append(li);
        }
        wrap.append(list);
        input.setAttribute("aria-expanded", "true");
      }, 250);
    });
    const box = h("div", { class: d.dataset.field || "field" }, h("label", { for: id }, d.dataset.label), wrap);
    box.__simValue = () => input.dataset.picked || "";
    return box;
  }

  // ---- Yes / No buttons ----------------------------------------------------------------------------------------------
  function yesno(d) {
    const choices = opts(d).length ? opts(d) : ["Yes", "No"];
    let value = "";
    const btns = choices.map((c) => {
      const b = h("button", { type: "button", class: "_option", "aria-pressed": "false" }, c);
      b.addEventListener("click", () => {
        value = c;
        for (const x of btns) { const on = x === b; x.setAttribute("aria-pressed", String(on)); x.className = on ? "_option _active" : "_option"; }
      });
      return b;
    });
    const box = h("div", { class: d.dataset.field || "_fieldEntry" }, h("label", { class: "_label" }, d.dataset.label), h("div", { class: "_yesno" }, btns));
    box.__simValue = () => value;
    return box;
  }

  // ---- select2 / chosen ----------------------------------------------------------------------------------------------
  function select2(d) {
    const id = d.dataset.id || `s2${++seq}`;
    const sel = h("select", { id, name: d.dataset.name || id, style: "display:none", tabindex: "-1", class: "select2-hidden-accessible" },
      h("option", { value: "" }, "Select..."), opts(d).map((o, i) => h("option", { value: `v${i}` }, o)));
    const rendered = h("span", { class: "select2-selection__rendered" }, "Select...");
    const box2 = h("span", { class: "select2 select2-container" }, h("span", { class: "select2-selection select2-selection--single", role: "combobox", tabindex: "0" }, rendered));
    sel.addEventListener("change", () => { rendered.textContent = sel.selectedIndex > 0 ? sel.options[sel.selectedIndex].textContent : "Select..."; });
    const box = h("div", { class: d.dataset.field || "field" }, h("label", { for: id }, d.dataset.label), sel, box2);
    box.__simValue = () => (rendered.textContent === "Select..." ? "" : rendered.textContent);
    return box;
  }

  // ---- international phone box -------------------------------------------------------------------------------------
  function iti(d) {
    const id = d.dataset.id || `tel${++seq}`;
    const input = h("input", { id, type: "tel", name: d.dataset.name || "phone", placeholder: "201-555-0123", autocomplete: "tel" });
    const box = h("div", { class: d.dataset.field || "field" }, h("label", { for: id }, d.dataset.label || "Phone"),
      h("div", { class: "iti iti--allow-dropdown" }, h("div", { class: "iti__flag-container" }, h("div", { class: "iti__selected-flag", title: "United States: +1" }, "🇺🇸 +1")), input));
    box.__simValue = () => input.value;
    return box;
  }

  // ---- a controlled (React) text box ---------------------------------------------------------------------------------
  function react(d) {
    const id = d.dataset.id || `r${++seq}`;
    const input = h(d.dataset.multiline != null ? "textarea" : "input", { id, name: d.dataset.name || null, type: d.dataset.multiline != null ? null : d.dataset.itype || "text",
      placeholder: d.dataset.placeholder || null, autocomplete: d.dataset.autocomplete || null });
    let state = "";
    input.addEventListener("input", () => { state = input.value; });
    input.addEventListener("blur", () => { if (input.value !== state) input.value = state; });
    const box = h("div", { class: d.dataset.field || "field" }, h("label", { for: id }, d.dataset.label), input);
    box.__simValue = () => state;
    return box;
  }

  // ---- repeaters -----------------------------------------------------------------------------------------------------
  function repeater(d) {
    const tpl = d.querySelector("template");
    const list = h("div", { class: "entries" });
    const add = h("button", { type: "button", class: "add-entry" }, d.dataset.add || "Add another");
    let n = 0;
    const one = () => {
      const i = n++;
      const html = tpl.innerHTML.replace(/\{i\}/g, String(i)).replace(/\{n\}/g, String(i + 1));
      const entry = h("div", { class: d.dataset.entry || "entry", "data-index": String(i) });
      entry.innerHTML = html;
      list.append(entry);
    };
    add.addEventListener("click", one);
    for (let k = 0; k < Number(d.dataset.start || 1); k++) one();
    const box = h("div", { class: d.dataset.field || "section" }, d.dataset.title ? h("h3", {}, d.dataset.title) : null, list, add);
    return box;
  }

  const BUILD = { rs, typeahead, yesno, select2, iti, react, repeater };
  for (const d of [...document.querySelectorAll("[data-w]")]) {
    const node = BUILD[d.dataset.w](d);
    for (const k of ["expect", "type", "sensitive"]) if (d.dataset[k] != null) node.dataset[k] = d.dataset[k];
    d.replaceWith(node);
  }

  // what a person sees in a checked element
  window.__simValue = (el) => {
    if (el.__simValue) return el.__simValue();
    if (el.matches("input[type=checkbox]")) return el.checked ? "checked" : "";
    if (el.matches("input[type=file]")) return el.files && el.files[0] ? el.files[0].name : "";
    if (el.matches("select")) return el.selectedIndex > 0 || (el.selectedIndex === 0 && el.options[0].value) ? el.options[el.selectedIndex].textContent.trim() : "";
    if (el.matches("input, textarea")) return el.value;
    const radios = [...el.querySelectorAll("input[type=radio]")];
    if (radios.length) {
      const on = radios.find((r) => r.checked);
      return on ? ((on.id && document.querySelector(`label[for="${on.id}"]`)) || on.closest("label") || {}).textContent.trim() : "";
    }
    const boxes = [...el.querySelectorAll("input[type=checkbox]")];
    if (boxes.length) return boxes.filter((b) => b.checked).map((b) => (b.closest("label") || {}).textContent.trim()).join("; ");
    return el.textContent.trim();
  };
  document.addEventListener("click", (e) => {
    const b = e.target.closest("button[type=submit], input[type=submit], [data-nav]");
    if (!b) return;
    S.clicks.push({ text: (b.textContent || b.value || "").trim(), trusted: e.isTrusted });
    e.preventDefault();
  }, true);
  document.addEventListener("submit", (e) => { S.clicks.push({ text: "form submit", trusted: e.isTrusted }); e.preventDefault(); }, true);
  S.ready = true;
})();

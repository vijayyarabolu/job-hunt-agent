// Job Hunt Agent Autofill, content script: production guards. Every adapter step runs with a time limit; a
// field is tried a few times per page at most (no loops); an error or a layout no adapter knows is reported to Job Hunt Agent
// with the form's HTML (no password, no ID / bank / birth-date values, never the job-site password), once per page and
// step. The caller then falls back to the plain (generic) filler, so the page keeps working.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const MAX_TRIES = 3;
  const MAX_HTML = 2900000;
  // GOAL31: the page goes as JSON (every " and newline escaped), and Job Hunt Agent takes 4 MB at most: a big Workday page
  // was refused while the panel said "sent"
  const MAX_JSON_BYTES = 3600000;
  const jsonBytes = (s) => new TextEncoder().encode(JSON.stringify(s)).length;
  // custom dropdowns show the picked answer as text (Greenhouse's react-select, Workday's prompts and pills)
  const PICKED = [".select__single-value", ".select__multi-value__label", "[class*=singleValue]", "[class*=multiValue] [class*=label]",
    "[data-automation-id=selectedItem]", "[data-automation-id=selectedItemList] li", "[data-automation-id^=formField-] button[aria-haspopup=listbox]"].join(", ");
  const tries = new Map();
  const sent = new Set();

  class Timeout extends Error {
    constructor(label, ms) { super(`${label} took longer than ${Math.round(ms / 1000)} s`); this.timeout = true; }
  }

  async function pageHtml() {
    const { account } = await JH.store(["account"]);
    const pw = (account && account.password) || "";
    const copy = document.documentElement.cloneNode(true);
    for (const s of copy.querySelectorAll("script, noscript, style[data-emotion], [data-jh-panel], [data-jh-marks], iframe[src*=captcha]")) s.remove();
    // the form's layout only: every typed value, ticked box and picked option is left out (a question's id may not
    // say it asks for an SSN or a birth date, and React copies what the user typed into the value attribute)
    for (const el of copy.querySelectorAll("input:not([type=submit]):not([type=button]):not([type=reset]), textarea")) {
      if (!/^(radio|checkbox)$/.test(el.type)) el.removeAttribute("value");   // a radio's value is the option, not the user's pick
      el.removeAttribute("checked");
      if (el.tagName === "TEXTAREA") el.textContent = "";
    }
    for (const o of copy.querySelectorAll("option[selected]")) o.removeAttribute("selected");
    for (const el of copy.querySelectorAll(`[contenteditable], ${PICKED}`)) el.textContent = "";
    for (const icon of copy.querySelectorAll("svg")) icon.replaceChildren();          // icons: no question is in them
    let html = copy.outerHTML;
    if (pw.length >= 4) for (const form of JH.secretForms(pw)) html = html.split(form).join("");
    html = html.slice(0, MAX_HTML);
    for (let n = jsonBytes(html); n > MAX_JSON_BYTES; n = jsonBytes(html)) html = html.slice(0, Math.floor(html.length * (MAX_JSON_BYTES / n) * 0.98));
    return html;
  }

  JH.guard = {
    Timeout,
    // fn() within ms, else a Timeout (the work may finish later; the panel and the page are not held up)
    async run(fn, ms, label = "this step") {
      let timer = null;
      try {
        return await Promise.race([Promise.resolve().then(fn), new Promise((_, reject) => { timer = setTimeout(() => reject(new Timeout(label, ms)), ms); })]);
      } finally {
        clearTimeout(timer);
      }
    },
    attempt(id) {                                   // false after MAX_TRIES fills of one field on this page
      const n = tries.get(id) || 0;
      if (n >= MAX_TRIES) return false;
      tries.set(id, n + 1);
      return true;
    },
    pageHtml,
    // one report per page, kind and step: GOAL25 §4 "no silent failures"
    async report(kind, message, b, step = "") {
      const mark = `${location.href}|${kind}|${step}|${String(message).slice(0, 80)}`;
      if (sent.has(mark)) return;
      sent.add(mark);
      let html = "";
      try { html = await pageHtml(); } catch { html = ""; }
      const version = JH.version;
      await JH.api("POST", "/api/autofill/error", { body: { board: (b && b.id) || "generic", step: String(step).slice(0, 60), kind,
        message: String(message || "").slice(0, 500), url: location.href, html, version } });
    },
  };
})();

// Job Hunt Agent Autofill, content script 1/n: shared helpers (window.JH). Runs in every frame, in the extension's
// isolated world: it sees the page's DOM, not its JavaScript. Values are set with the element's own native setter
// plus input/change/blur events, which React, Angular and Workday all pick up.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

JH.sleep = (ms) => new Promise((r) => setTimeout(r, ms));
JH.norm = (s) => String(s == null ? "" : s).toLowerCase().replace(/[’']/g, "'").replace(/[^a-z0-9+]+/g, " ").trim();
JH.text = (el) => String((el && (el.innerText || el.textContent)) || "").replace(/\s+/g, " ").trim();
JH.isTop = window.top === window;

// Shadow roots too (SmartRecruiters' web components): every element matching `sel` under `root`.
JH.all = (sel, root = document) => {
  const out = [...root.querySelectorAll(sel)];
  for (const host of root.querySelectorAll("*")) {
    if (host.shadowRoot && !host.hasAttribute("data-jh-panel")) out.push(...JH.all(sel, host.shadowRoot));
  }
  return out;
};
JH.one = (sel, root = document) => JH.all(sel, root)[0] || null;

JH.visible = (el) => {
  if (!el || !el.isConnected) return false;
  const st = getComputedStyle(el);
  if (st.display === "none" || st.visibility === "hidden" || st.visibility === "collapse") return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
};

// A control the person can see: itself, or (a styled radio / checkbox / file input) its label or wrapper.
JH.shown = (el) => {
  if (JH.visible(el)) return true;
  if (!/^(radio|checkbox|file)$/.test(el.type || "")) return false;
  const label = (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)) || el.closest("label") || el.parentElement;
  return JH.visible(label);
};

const HONEYPOT = /beecatcher|honeypot|hp[-_]?field|for robots|robots only|do not (enter|fill|type)|leave (this )?(field )?(blank|empty)|not for humans/i;
JH.isHoneypot = (el, label = "") => {
  const attrs = [el.name, el.id, el.className, el.getAttribute("data-automation-id"), el.getAttribute("aria-label"), label].join(" ");
  if (HONEYPOT.test(attrs)) return true;
  if (el.closest("[aria-hidden='true']") && el.type !== "file" && el.type !== "radio" && el.type !== "checkbox") return true;
  const r = el.getBoundingClientRect();
  return el.tabIndex < 0 && r.width <= 2 && r.height <= 2 && el.type !== "file";
};

// Never Submit / Next / Sign in for the user. Every click this extension makes goes through JH.click.
const FORBIDDEN_IDS = /^(bottom-navigation-next-button|bottom-navigation-footer-button|pagefooternextbutton|createaccountsubmitbutton|signinsubmitbutton|click_filter|submit|wd-commandbutton.*submit)/i;
const FORBIDDEN_TEXT = /^(submit( (my )?application)?|send( my)? application|next|next step|continue|save (and|&) continue|save and next|review( and submit)?|finish|apply( now)?|apply for this job|sign ?in|log ?in|create account|register|complete application)\b/i;
JH.isForbidden = (el) => {
  if (!el) return true;
  const btn = el.closest("button, [role=button], input[type=submit], input[type=button], a") || el;
  if (btn.matches("[aria-haspopup], [role=option], [role=tab], label, input[type=radio], input[type=checkbox]")) return false;
  if (el.matches("[role=option], [role=menuitem], [role=radio], [role=checkbox], li")) return false;
  const id = btn.getAttribute("data-automation-id") || btn.id || "";
  if (FORBIDDEN_IDS.test(id)) return true;
  if (btn.matches("input[type=submit]") || (btn.tagName === "BUTTON" && btn.type === "submit" && btn.form && !btn.getAttribute("data-automation-id"))) {
    return true;                                         // a plain form's submit button, whatever it says
  }
  return FORBIDDEN_TEXT.test(JH.text(btn) || btn.value || btn.getAttribute("aria-label") || "");
};

JH.click = (el, { allow = false } = {}) => {
  if (!el) return false;
  if (!allow && JH.isForbidden(el)) {
    console.warn("Job Hunt Agent Autofill: refused to click", el);
    return false;
  }
  const opts = { bubbles: true, cancelable: true, view: window, button: 0 };
  el.dispatchEvent(new PointerEvent("pointerdown", { ...opts, pointerType: "mouse" }));
  el.dispatchEvent(new MouseEvent("mousedown", opts));
  el.dispatchEvent(new PointerEvent("pointerup", { ...opts, pointerType: "mouse" }));
  el.dispatchEvent(new MouseEvent("mouseup", opts));
  el.click();
  return true;
};

JH.key = (el, key, type = "both") => {
  const codes = { Enter: 13, Tab: 9, ArrowDown: 40, ArrowUp: 38, Escape: 27, Backspace: 8 };
  const init = { key, code: key, keyCode: codes[key] || key.charCodeAt(0), which: codes[key] || key.charCodeAt(0), bubbles: true, cancelable: true };
  if (type !== "up") el.dispatchEvent(new KeyboardEvent("keydown", init));
  if (type !== "down") el.dispatchEvent(new KeyboardEvent("keyup", init));
};

function nativeSetter(el) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
    : el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
  return Object.getOwnPropertyDescriptor(proto, "value").set;
}

JH.setValue = (el, value, { blur = true } = {}) => {
  el.focus({ preventScroll: true });
  nativeSetter(el).call(el, value);
  el.dispatchEvent(new InputEvent("input", { bubbles: true, data: String(value), inputType: "insertText" }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  if (blur) {
    el.dispatchEvent(new FocusEvent("blur", { bubbles: false }));
    el.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
  }
  JH.mark(el);
  return el.value === String(value);
};

// Type into a search box (typeaheads): one input event per character, like a person.
JH.type = async (el, text) => {
  el.focus({ preventScroll: true });
  nativeSetter(el).call(el, "");
  el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "deleteContentBackward" }));
  for (const ch of String(text)) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: ch, bubbles: true }));
    nativeSetter(el).call(el, el.value + ch);
    el.dispatchEvent(new InputEvent("input", { bubbles: true, data: ch, inputType: "insertText" }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: ch, bubbles: true }));
    await JH.sleep(8);
  }
};

JH.waitFor = async (fn, timeout = 3000, every = 80) => {
  const end = Date.now() + timeout;
  for (;;) {
    let got = null;
    try { got = fn(); } catch { got = null; }
    if (got) return got;
    if (Date.now() > end) return null;
    await JH.sleep(every);
  }
};

// Wait until the page stops changing (Workday re-renders after every pick, and parses an uploaded resume).
JH.settle = (quietMs = 400, maxMs = 6000) => new Promise((resolve) => {
  let timer = null;
  const done = () => { obs.disconnect(); clearTimeout(cap); resolve(); };
  const obs = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(done, quietMs); });
  obs.observe(document.documentElement, { childList: true, subtree: true, attributes: true, characterData: true });
  timer = setTimeout(done, quietMs);
  const cap = setTimeout(done, maxMs);
});

// Fields the person typed into themselves are never overwritten (trusted events only come from a real person).
// Also by the field's own id / name, so a field Workday re-renders (or the user comes Back to) is still the user's.
const HAND = new WeakSet();
const HAND_KEYS = new Set();
const OURS = new WeakSet();
const handKey = (el) => el.getAttribute("data-automation-id") ? `a:${el.getAttribute("data-automation-id")}:${el.id || ""}` : el.id ? `i:${el.id}` : el.name ? `n:${el.name}` : "";
// A radio / checkbox Autofill clicks fires a "trusted" input + change (the browser's own activation events), so a
// change to one counts as the user's only right after a real press or key.
const GESTURE_MS = 1500;
let lastGesture = 0;
const onPanel = (e) => e.target instanceof Element && Boolean(e.target.closest("[data-jh-panel]"));   // the panel's Autofill is not a press on the page
for (const type of ["pointerdown", "keydown"]) document.addEventListener(type, (e) => { if (e.isTrusted && !onPanel(e)) lastGesture = Date.now(); }, true);
JH.realEvent = (e) => e.isTrusted && !(e.target instanceof HTMLInputElement && /^(radio|checkbox)$/.test(e.target.type) &&
  Date.now() - lastGesture > GESTURE_MS);
for (const type of ["input", "change"]) {
  document.addEventListener(type, (e) => {
    if (!JH.realEvent(e) || !(e.target instanceof Element)) return;
    HAND.add(e.target);
    const k = handKey(e.target);
    if (k) HAND_KEYS.add(k);
  }, true);
}
// the job-site password as it can appear in page HTML or a request body (raw, HTML-, JSON- and URL-escaped)
JH.secretForms = (pw) => {
  const html = pw.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return [...new Set([pw, html, html.replace(/'/g, "&#39;"), JSON.stringify(pw).slice(1, -1), encodeURIComponent(pw)])].filter(Boolean);
};
JH.byHand = (el) => HAND.has(el) || (handKey(el) !== "" && HAND_KEYS.has(handKey(el)));
JH.mark = (el) => { OURS.add(el); el.setAttribute("data-jh-filled", "1"); };
JH.ours = (el) => OURS.has(el);

JH.today = () => {
  const d = new Date();
  return { mm: String(d.getMonth() + 1).padStart(2, "0"), dd: String(d.getDate()).padStart(2, "0"), yyyy: String(d.getFullYear()) };
};

JH.b64File = (b64, name, type = "application/pdf") => {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new File([bytes], name, { type, lastModified: Date.now() });
};

// A file input gets the resume the way a drop would give it (DataTransfer), then change + input.
JH.setFile = (input, file) => {
  const dt = new DataTransfer();
  dt.items.add(file);
  input.files = dt.files;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  JH.mark(input);
  return input.files.length === 1;
};

// The page's own JavaScript world (page/bridge.js): React handlers the isolated world cannot reach.
JH.react = (el, op, arg = "") => new Promise((resolve) => {
  const tag = "jh" + Math.random().toString(36).slice(2);
  el.setAttribute("data-jh-bridge", tag);
  const done = (e) => {
    if (e.detail !== tag) return;
    document.removeEventListener("jh-bridge-done", done);
    el.removeAttribute("data-jh-bridge");
    resolve(el.getAttribute("data-jh-bridge-result") === "ok");
  };
  document.addEventListener("jh-bridge-done", done);
  el.setAttribute("data-jh-bridge-arg", String(arg));
  document.dispatchEvent(new CustomEvent("jh-bridge", { detail: `${tag}|${op}` }));
  setTimeout(() => { document.removeEventListener("jh-bridge-done", done); resolve(false); }, 1500);
});

JH.send = (msg) => new Promise((resolve) => {
  try {
    chrome.runtime.sendMessage(msg, (res) => resolve(chrome.runtime.lastError ? { ok: false, error: "extension reloaded: refresh this page" } : res));
  } catch {
    resolve({ ok: false, error: "extension reloaded: refresh this page" });
  }
});
JH.api = (method, path, { query, body, binary } = {}) => JH.send({ type: "api", method, path, query, body, binary });
JH.store = (keys) => new Promise((resolve) => chrome.storage.local.get(keys, resolve));

// A human check. "challenge": a puzzle or a full-page check (reCAPTCHA / hCaptcha challenge, Turnstile, Arkose,
// DataDome) is on screen: nothing is filled until the user solves it. "box": an "I'm not a robot" box sits in the form: the
// form is filled, the box is the user's to tick. Invisible badges do not count. Never clicked, never solved, never bypassed.
JH.captcha = () => {
  const frames = [...document.querySelectorAll("iframe")];
  const big = frames.find((f) => {
    if (!/recaptcha\/(api2|enterprise)\/bframe|hcaptcha\.com\/.*(challenge|frame=challenge)|challenges\.cloudflare\.com|arkoselabs|funcaptcha|captcha-delivery\.com|geo\.captcha/i.test(f.src || "")) return false;
    const r = f.getBoundingClientRect();
    return JH.visible(f) && r.width > 100 && r.height > 100;
  });
  if (big) return "challenge";
  const box = [...document.querySelectorAll(".g-recaptcha, .h-captcha, .cf-turnstile, iframe[src*='recaptcha/api2/anchor'], iframe[src*='recaptcha/enterprise/anchor'], iframe[src*='hcaptcha.com'][src*='checkbox']")]
    .find((el) => { const r = el.getBoundingClientRect(); return JH.visible(el) && r.width > 60 && r.height > 30 && !el.closest(".grecaptcha-badge"); });
  return box ? "box" : null;
};

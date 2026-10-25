// Core: state, DOM, API, toasts, popover; job text as textContent.

export const $ = (s, root = document) => root.querySelector(s);
export const $$ = (s, root = document) => [...root.querySelectorAll(s)];
export const calm = matchMedia("(prefers-reduced-motion: reduce)");
export const phone = matchMedia("(max-width: 760px)");

// localStorage can throw (private mode): guarded
export const store = {
  get(key, fallback = "") { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* not remembered */ } },
  json(key, fallback) {
    try { const v = JSON.parse(localStorage.getItem(key) || "null"); return v && typeof v === "object" ? v : fallback; }
    catch { return fallback; }
  },
};

// the side menu's order (number keys 1 to 9 follow it) and its sections (the phone's bottom bar)
export const TABS = ["recommended", "saved", "archived", "resumes", "tracker", "applied", "interviewing", "followups", "stats", "answers"];
export const SECTIONS = [["Jobs", ["recommended", "saved", "archived"]], ["Resumes", ["resumes"]],
  ["Applications", ["tracker", "applied", "interviewing", "followups"]], ["Stats", ["stats"]], ["Autofill", ["answers"]]];
export const sectionOf = (tab) => SECTIONS.find(([, tabs]) => tabs.includes(tab)) || SECTIONS[0];
export const JOB_TABS = ["recommended", "saved", "archived", "resumes", "applied"];
export const TABLE_TABS = ["interviewing", "tracker"];
export const STATUS_LABELS = { saved: "Saved", applied: "Applied", screen: "Screen", interview: "Interview",
  offer: "Offer", rejected: "Rejected", ghosted: "Cold" };   // W14: the word the Sheet uses (Status(A/R))
export const FAMILY_LABELS = { ai_engineer: "AI Engineer", genai_engineer: "GenAI Engineer", ml_engineer: "ML Engineer",
  dl_engineer: "Deep Learning", data_scientist: "Data Scientist", data_engineer: "Data Engineer", other: "Other" };
export const WORKPLACES = [["remote", "Remote"], ["hybrid", "Hybrid"], ["onsite", "Onsite"]];
export const EMPLOYMENTS = [["full_time", "Full-time"], ["contract", "Contract"], ["contract_to_hire", "Contract-to-hire"]];
export const OPEN_TIP = "Open the row editor: edit, contacts, outreach";
export const FIND_TIP = "Find contacts: free signals first, Apollo / Hunter only on click";
export const menu = (...kids) => h("div", { class: "pop-body", role: "menu" }, ...kids);
export const WORTH_TIP = "Priority, 0 to 100: what to apply to first. Your Match % × how fresh the posting is × company " +
  "signals (posted pay, a named team, hiring momentum). 70+ means apply today.";
export const MATCH_TIP = "Match: how well your best resume fits this job, in %. The outer ring splits it into " +
  "experience, skills, industry and title; plus JD GenAI work.";

export const S = {
  profile: store.get("jh.profile"), profiles: [], tab: "recommended",
  items: [], cur: 0, filters: {}, detail: null, google: null, sheetUrl: null, followups: 0, rq: "ready", place: {},
};

export function h(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(typeof attrs === "string" ? { class: attrs } : attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "style") node.style.cssText = v;     // GOAL31: CSP-safe
    else node.setAttribute(k, v === true ? "" : v);
  }
  node.append(...kids.flat(9).filter((x) => x != null && x !== false));
  return node;
}
export const B = (attrs, ...kids) => h("button", { type: "button", ...attrs }, ...kids);
export const ghost = (onclick, ...kids) => B({ class: "btn ghost", onclick }, ...kids);
export const btn = (cls, title, onclick, ...kids) => B({ class: `btn ${cls}`.trim(), title, onclick }, ...kids);
const NS = "http://www.w3.org/2000/svg";
export function sv(tag, attrs) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}
export function icon(id, cls = "ic") {
  const s = sv("svg", { class: cls, "aria-hidden": "true" });
  s.append(sv("use", { href: `#i-${id}` }));
  return s;
}
export const sm = (id) => icon(id, "ic sm");
export const iconBtn = (label, id, onclick, attrs = {}) => B({ class: "icon-btn", "aria-label": label, title: label, onclick, ...attrs }, icon(id));
export const muted = (text, cls = "muted") => h("span", cls, text);
export const still = (el) => el?.focus({ preventScroll: true });
export const mini = (label, title, fn, cls = "") => B({ class: `mini ${cls}`, title, onclick: (e) => { e.stopPropagation(); fn(e); },
  ...(typeof label === "string" && !/[a-z]/i.test(label) ? { "aria-label": title } : {}) }, label);   // "×" spoken

const safeUrl = (url) => (/^https?:\/\//i.test(url || "") ? url : "#");
export const openExt = (url) => { if (safeUrl(url) !== "#") window.open(url, "_blank", "noopener"); };
export const extLink = (url, text, cls, title) => h("a", { href: safeUrl(url), target: "_blank", rel: "noopener noreferrer", class: cls, title }, text);
export const fullJd = (p) => h("a", { href: `/jd?${q(p)}`, target: "_blank", rel: "noopener", class: "mini", title: "The full JD: read, copy, print" }, "Full JD");

export async function api(path, body, file) {
  const opts = body || file ? { method: "POST", headers: { "Content-Type": file ? file.type || "application/octet-stream" : "application/json",
    "X-Jobhunt": "1" }, body: file || JSON.stringify({ profile: S.profile, ...body }) } : {};
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || `HTTP ${res.status}`), { status: res.status });
  return data;
}
export const q = (params = {}) => new URLSearchParams({ profile: S.profile, ...params }).toString();
export const get = (path, params) => api(`${path}?${q(params)}`);
export const tabName = (t) => $(`#tabs [data-tab="${t}"] .lbl`)?.textContent || t;

const TOAST_MS = 6000;
const ERROR_MS = 9000;
export function toast(msg, { undo, error, extra, ms } = {}) {
  const zone = $("#toasts");
  const t = h("div", `toast${error ? " err" : ""}`, error ? icon("ban", "ic t-err") : null, h("p", {}, msg), extra);
  const close = () => { t.classList.add("out"); setTimeout(() => t.remove(), calm.matches ? 0 : 180); };
  let back = null;
  t.addEventListener("focusin", (e) => { if (!t.contains(e.relatedTarget)) back = e.relatedTarget; });
  if (undo) t.append(B({ class: "undo", title: "Undo this", onclick: () => { close(); if (back?.isConnected) still(back); undo(); } }, "Undo"));
  t.append(B({ class: "icon-btn sm", "aria-label": "Dismiss", title: "Dismiss this message", onclick: close }, sm("x")));
  zone.append(t);
  while (zone.children.length > 3) zone.firstElementChild.remove();
  const wait = () => setTimeout(() => (t.contains(document.activeElement) ? wait() : close()), ms || (error || undo ? ERROR_MS : TOAST_MS));
  wait();
  return t;
}
export const fail = (err) => toast(err.message || String(err), { error: true });

// GOAL31 §3: a waiting page shows its shape; a failed one says why, with Retry
const PLACEHOLDER_WIDTHS = ["w70", "w40", "w90", "w50", "w80", "w40"];
export const placeholder = (lines = PLACEHOLDER_WIDTHS.length) => h("div", { class: "pad ph", "aria-busy": "true", "aria-label": "Loading" },
  ...Array.from({ length: lines }, (_, i) => h("span", `sk-line ${PLACEHOLDER_WIDTHS[i % PLACEHOLDER_WIDTHS.length]}`)));
const retryBtn = (retry) => B({ class: "lnk", title: "Try loading it again", onclick: retry }, "Retry");
export const loadError = (err, retry) => h("p", { class: "pad load-err", role: "alert" }, icon("ban", "ic sm"), ` Could not load: ${err.message} `, retryBtn(retry));
export const refreshFailed = (err, retry) => toast(`Could not refresh: ${err.message}. Showing what was loaded before.`, { error: true, extra: retryBtn(retry) });

export async function optimistic(apply, rollback, request, okMsg, undo) {
  apply();
  try {
    const out = await request();
    if (okMsg) toast(okMsg, { undo });
    return out;
  } catch (err) {
    rollback();
    toast(`Not saved: ${err.message}. Put back as it was.`, { error: true });
    return null;
  }
}

let popAnchor = null;
// Popovers close on pick, click away, Esc, blur, scroll, resize.
export function openPop(content, anchor, label) {
  const pop = $("#pop");
  if (popAnchor === anchor && !pop.hidden) { closePop(); return; }
  closePop({ refocus: false });
  pop.replaceChildren(content);
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-label", label || anchor.textContent.trim() || "Options");
  pop.hidden = false;
  popAnchor = anchor;
  anchor.setAttribute("aria-expanded", "true");
  const r = anchor.getBoundingClientRect();
  popAt = r;
  const above = r.bottom + pop.offsetHeight + 8 > innerHeight;
  pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - pop.offsetWidth - 8))}px`;
  pop.style.top = `${Math.max(8, above ? r.top - pop.offsetHeight - 6 : r.bottom + 6)}px`;
  (pop.querySelector("input, button, select") || pop).focus();
}
export function closePop({ refocus = true } = {}) {
  const pop = $("#pop");
  if (pop.hidden) return false;
  pop.hidden = true;
  if (popAnchor) { popAnchor.setAttribute("aria-expanded", "false"); if (refocus) still(popAnchor); }
  popAnchor = null;
  return true;
}
export const popOpen = () => !$("#pop").hidden;
const outsidePop = (t) => popAnchor && t instanceof Node && !$("#pop").contains(t) && !popAnchor.contains(t);
document.addEventListener("pointerdown", (e) => { if (outsidePop(e.target)) closePop({ refocus: false }); }, true);
document.addEventListener("focusin", (e) => { if (outsidePop(e.target)) closePop({ refocus: false }); });
const ANCHOR_SLACK_PX = 2;
let popAt = null;
const moved = () => { const r = popAnchor.getBoundingClientRect(); return Math.abs(r.left - popAt.left) + Math.abs(r.top - popAt.top) > ANCHOR_SLACK_PX; };
const movesAnchor = (t) => (t === document || (t instanceof Node && t.contains(popAnchor))) && moved();
document.addEventListener("scroll", (e) => { if (popAnchor && movesAnchor(e.target)) closePop({ refocus: false }); }, { capture: true, passive: true });
addEventListener("resize", () => closePop({ refocus: false }));
export const titled = (el, title) => { el.title = title; return el; };
export const opt = (text, onclick, pressed, { keep = false } = {}) => B({ class: "opt", "aria-pressed": pressed == null ? null : String(!!pressed),
  onclick: () => { if (!keep) closePop(); onclick(); } }, h("span", "tick", sm("check")), h("span", {}, text));

export function ask(title, text, okLabel = "OK") {
  const dlg = $("#ask");
  $("#ask-h").textContent = title;
  $("#ask-p").textContent = text;
  $("#ask-ok").textContent = okLabel;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((done) => dlg.addEventListener("close", () => done(dlg.returnValue === "yes"), { once: true }));
}

export function copyText(text, okMsg) {
  const fallback = () => {
    const box = h("textarea", { readonly: true, class: "offscreen" });
    box.value = text;
    document.body.append(box);
    box.select();
    let done = false;
    try { done = document.execCommand("copy"); } catch { done = false; }
    box.remove();
    if (done) toast(okMsg); else showCopyBox(text);
  };
  if (window.isSecureContext && navigator.clipboard) navigator.clipboard.writeText(text).then(() => toast(okMsg), fallback);
  else fallback();
}
function showCopyBox(text) {
  const box = h("textarea", { class: "copybox", readonly: true, "aria-label": "Copy this text" });
  box.value = text;
  const panel = h("div", "copy-panel", h("p", {}, "Select all and copy:"), box,
    ghost(() => panel.remove(), "Close"));
  document.body.append(panel);
  box.focus();
  box.select();
}

export const tier = (p) => (p == null ? "na" : p >= 80 ? "hi" : p >= 60 ? "mid" : "lo");
const locParts = (loc) => (loc || "").split(/\s*;\s*/).filter(Boolean);
const titleCase = (s) => (s === s.toUpperCase() && s.length > 3 ? s.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()) : s);
export const STATES = Object.fromEntries(("alabama AL alaska AK arizona AZ arkansas AR california CA colorado CO connecticut CT " +
  "delaware DE florida FL georgia GA hawaii HI idaho ID illinois IL indiana IN iowa IA kansas KS kentucky KY louisiana LA " +
  "maine ME maryland MD massachusetts MA michigan MI minnesota MN mississippi MS missouri MO montana MT nebraska NE " +
  "nevada NV new_hampshire NH new_jersey NJ new_mexico NM new_york NY north_carolina NC north_dakota ND ohio OH " +
  "oklahoma OK oregon OR pennsylvania PA rhode_island RI south_carolina SC south_dakota SD tennessee TN texas TX utah UT " +
  "vermont VT virginia VA washington WA west_virginia WV wisconsin WI wyoming WY district_of_columbia DC")
  .split(" ").flatMap((w, i, all) => (i % 2 ? [] : [[w.replace(/_/g, " "), all[i + 1]]])));
function oneLoc(raw) {
  const s = raw.trim().replace(/,?\s*(United States of America|United States|USA|US)$/i, "");
  if (/^remote/i.test(s)) return "Remote";
  let m = s.match(/^US\s*[,-]\s*([A-Z]{2})\s*[,-]\s*(.+)$/);
  if (m) return `${titleCase(m[2])}, ${m[1]}`;
  m = s.match(/^(.+?),\s*([A-Za-z .]+)$/);
  const st = m && (STATES[m[2].trim().toLowerCase()] || (/^[A-Z]{2}$/.test(m[2].trim()) ? m[2].trim() : null));
  return st ? `${titleCase(m[1].split(",")[0])}, ${st}` : s || raw.trim();
}
export function shortLoc(loc) {
  const p = locParts(loc);
  return !p.length ? "" : p.length > 1 ? `${oneLoc(p[0])} +${p.length - 1} more` : oneLoc(p[0]);
}
export const locSpan = (loc, cls) => h("span", { class: cls, title: locParts(loc).join("\n") }, shortLoc(loc));
export function exactAge(ts, exact = true) {
  const minutes = Math.max(1, Math.floor((Date.now() - Date.parse(ts)) / 60000));
  const hr = Math.floor(minutes / 60), d = Math.floor(minutes / 1440);
  const text = minutes < 60 ? `${minutes} min ago` : hr < 24 ? `${hr} h ${minutes % 60} min ago`
    : d < 7 ? `${d} d ${hr % 24} h ago` : `${d} d ago`;
  return exact ? text : `~${text}`;
}
// GOAL32 §2: "Posted today, found 9:03 AM" when the board gives only a day; "Found 2 h ago, posted date unknown"
const localDay = (ms) => { const d = new Date(ms); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const dayWord = (day) => (day === localDay(Date.now()) ? "today" : day === localDay(Date.now() - 86400000) ? "yesterday" : shortDate(day));
export function postedText(ts, exact, found, day) {
  if (found === "day") {
    const seen = localDay(Date.parse(ts));                 // the board's day is a UTC date: never later than when found
    const posted = day && day < seen ? day : seen;
    const clock = new Date(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return `Posted ${dayWord(posted)}, found ${posted === seen ? "" : `${dayWord(seen)} `}${clock}`;
  }
  if (found === "unknown") return `Found ${exactAge(ts)}, posted date unknown`;
  return exactAge(ts, exact).replace(" ago", "");
}
export function shortDate(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${m}/${d}/${y.slice(2)}`;
}
export const isoToday = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
export const fieldName = (f) => f.replace(/^sheet:/, "").replace(/_/g, " ");

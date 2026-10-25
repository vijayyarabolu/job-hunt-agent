// Applied as cards (user, 2026-10-16), lazy from feed.js: the jobs you applied to, by the day you applied; search finds
// the one an email is about, and the pane shows its JD, the resume you sent and the tracker row. Tracker keeps the table.
import { $, B, S, STATUS_LABELS, h, muted } from "./core.js";

const SEGS = [["all", "All", "Every application you sent"], ["waiting", "Waiting", "Applied, no answer yet"],
  ["interviewing", "Interviewing", "Screen, interview or offer"], ["rejected", "Ended", "Rejected, or no answer for weeks (Cold)"]];
const GLYPH = { applied: "○", screen: "◆", interview: "◆", offer: "★", rejected: "✕", ghosted: "◌" };
const SEARCH_MS = 250;
let search = null, timer = null;
// status = glyph + word, never colour alone (readable with red-green colour blindness)
const sheet = new CSSStyleSheet();   // the CSP blocks <style>; app.css is at its cap
sheet.replaceSync(`.ap-search { display: block; width: 100%; height: 34px; margin: 8px 0 0; padding: 0 12px; border: var(--line); border-radius: var(--r-pill); background: var(--raised); color: var(--ink); font: inherit }
#list-head .mail-box { margin: 8px 0 0; max-height: 40dvh; overflow-y: auto }
.row-side .ap-st { white-space: nowrap; font-weight: 650 }
.row-side .ap-rejected, .row-side .ap-ghosted { color: var(--quiet) }
.row-side .ap-screen, .row-side .ap-interview, .row-side .ap-offer { color: var(--ink); border: 1.5px solid currentColor }`);
document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];

// GOAL35 §5: what was searched, and one click to the other segments or a cleared search
export function empty(meta = {}, loadFeed = () => {}) {
  if (!S.apq) return "No applications here yet: mark a job applied (m) and it shows here.";
  const other = meta.other_segments || 0;
  const clear = () => { S.apq = ""; if (search) search.value = ""; S.cur = 0; loadFeed(); };
  return [`No application matches “${S.apq}”${(S.ap || "all") !== "all" ? " in this segment" : ""}. Searched company, role, city, JD text and dates. `,
    other ? B({ class: "linklike", onclick: () => { S.ap = "all"; S.cur = 0; loadFeed(); } }, `Show the ${other} in other segments`) : null,
    other ? " · " : null, B({ class: "linklike", onclick: clear }, "Clear search")];
}
export const params = () => ({ tab: "applied", view: "cards", seg: S.ap || "all", q: S.apq || "" });

export function head(meta, loadFeed) {
  const c = meta.counts || {};
  search ||= h("input", { type: "search", class: "ap-search", value: S.apq || "", placeholder: "Search company or role (an email's company)",
    "aria-label": "Search your applications", oninput: (e) => {
      clearTimeout(timer);
      const v = e.target.value.trim();
      timer = setTimeout(() => { S.apq = v; S.cur = 0; loadFeed({ quiet: true }); }, SEARCH_MS);
    } });
  const typing = document.activeElement === search;
  if (!typing && search.value.trim() !== (S.apq || "")) search.value = S.apq || "";   // back / forward changed it
  $("#list-head").replaceChildren(
    h("div", "lh-top", h("h1", { id: "lh-title" }, "Applied ", muted(`${meta.total ?? S.items.length}${S.apq ? " found" : ""}`)),
      B({ class: "lnk", title: "The same applications as the table you edit", onclick: () => S.switchTab("tracker") }, "Table view ›")),
    h("div", { class: "seg rq-seg", role: "group", "aria-label": "Show" }, SEGS.map(([v, label, tip]) =>
      B({ "aria-pressed": String((S.ap || "all") === v), title: tip, onclick: () => { S.ap = v; S.cur = 0; loadFeed(); } }, `${label} ${c[v] || 0}`))),
    search, mail());
  if (typing) { search.focus(); search.setSelectionRange(search.value.length, search.value.length); }
}

// the Tracker's email box (Email today: rejected · need you)
function mail() {
  if (S.mail) return S.mail;
  const slot = h("div", "mail-slot");
  import("./tracker.js").then((m) => { S.mail ||= m.mailMount(h("div", "mail-box"), "panel"); if (slot.isConnected) slot.replaceWith(S.mail); });
  return slot;
}

export function side(job) {
  const st = job.ap.status, word = STATUS_LABELS[st] || st;
  return h("div", "row-side",
    h("span", { class: `tag ap-st ap-${st}`, title: `Status: ${word}` }, `${GLYPH[st] || "•"} ${word}`),
    job.ap.time ? h("span", { class: "muted small", title: `Applied ${job.ap.applied_on} at ${job.ap.time}` }, job.ap.time) : null,
    job.outside ? h("span", { class: "tag soft", title: "Added by link or pasted in the Resume app: opens its tracker row" }, "outside job") : null);
}

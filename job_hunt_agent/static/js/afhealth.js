// Autofill must not fail silently. Settings › Autofill › Health shows the last 7 days (fill rate per
// board, errors grouped by board + step, the questions it could not answer, the nightly check), and the app says when
// it opens that the nightly check found a drop or that a newer extension is ready (each person's own update steps).
import { B, S, api, get, h, toast } from "./core.js";

const NOTICE_MS = 60000;
const SEEN_KEY = "jh.extnote";
const when = (iso) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");
const KIND = { error: "Error", timeout: "Took too long", unknown_layout: "Page it did not know" };

function css() {
  if (!document.querySelector('link[href="/answers.css"]')) document.head.append(h("link", { rel: "stylesheet", href: "/answers.css" }));
}

// how to update: a downloaded copy is replaced; a folder loaded from the clone only needs a reload
export function updateSteps() {
  return "Download it again (Settings › Autofill extension › Get the Autofill extension), unzip it over the old " +
    "job-hunt-agent-autofill folder, then click ↻ on Job Hunt Agent Autofill in chrome://extensions. If you loaded the " +
    "extension/ folder of your clone instead, pull the new code and click ↻.";
}

const table = (head, rows) => h("div", "af-tablewrap", h("table", "af-table", h("thead", {}, h("tr", {}, head.map((t) => h("th", {}, t)))), h("tbody", {}, rows)));

function boards(list) {
  if (!list.length) return h("p", "hint", "No fills in the last 7 days.");
  return table(["Board", "Filled by Autofill", "By you", "Needs you", "Fill rate"], list.map((b) => h("tr", {},
    h("td", {}, b.board), h("td", "num", String(b.filled)), h("td", "num", String(b.hand)), h("td", "num", String(b.needs)),
    h("td", "num", b.rate == null ? "–" : `${b.rate}%`))));
}

function errors(list) {
  if (!list.length) return h("p", "hint", "✓ No errors in the last 7 days.");
  return table(["Board · step", "What", "Times", "Last", "Message"], list.map((e) => h("tr", {},
    h("td", {}, `${e.board}${e.step ? ` · ${e.step}` : ""}`), h("td", {}, `▲ ${KIND[e.kind] || e.kind}`), h("td", "num", String(e.n)),
    h("td", {}, when(e.last_at)), h("td", {}, `${String(e.message || "").split("\n")[0].slice(0, 160)}${e.version ? ` (v${e.version})` : ""}`))));
}

function questions(list) {
  if (!list.length) return h("p", "hint", "None waiting.");
  return h("ul", "af-list", list.map((x) => h("li", {}, h("span", {}, x.text), h("span", "hint", ` · asked ${x.seen}× · ${x.boards.join(", ")}`))));
}

function canary(c) {
  if (!c) return h("p", "hint", "The nightly check has not run yet (it runs about 3 AM).");
  const bad = c.pages.filter((p) => !p.ok);
  return h("div", {}, h("p", {}, `${bad.length ? `▲ ${bad.length} page(s) dropped` : "✓ Every page filled as before"} · last run ${when(c.at)} · ${c.pages.length} page(s)`),
    table(["Page", "Board", "Filled", "Result"], c.pages.map((p) => h("tr", {}, h("td", {}, p.name), h("td", {}, p.board),
      h("td", "num", `${p.filled} of ${p.fields}`), h("td", {}, p.ok ? "✓ OK" : `▲ Dropped${p.detail ? `: ${p.detail.slice(0, 120)}` : ""}`)))));
}

function version(x) {
  const has = x.installed ? `Your Chrome uses ${x.installed}` : "Your Chrome has not used it yet";
  return h("div", {}, h("p", {}, `This app has Autofill ${x.current}. ${has}${x.seen_at ? ` (seen ${when(x.seen_at)})` : ""}.`),
    x.newer ? h("p", {}, h("strong", {}, "▲ Update ready: "), updateSteps()) : null);
}

export function healthCard() {
  css();
  const body = h("div", { class: "af-health", "aria-live": "polite" }, h("p", "hint", "Loading…"));
  get("/api/autofill/health/report").then((r) => {
    body.replaceChildren(h("h4", {}, "Version"), version(r.extension), h("h4", {}, `Fill rate per board (last ${r.days} days)`), boards(r.boards),
      h("h4", {}, "Errors and pages it did not know"), errors(r.errors), h("h4", {}, "Questions it could not answer"), questions(r.questions),
      h("h4", {}, "Nightly check"), canary(r.canary));
  }).catch((err) => body.replaceChildren(h("p", {}, `Health could not load: ${err.message}`)));
  return h("section", "set-card", h("h3", {}, "Health"), h("p", "hint", "Every error and every page layout Autofill does not know is saved here on its own (never a password or ID), so it can be fixed."), body);
}

// when the app opens: a drop the nightly check found (until "Got it") and a newer extension (once per version)
export async function initNotices() {
  let r;
  try { r = await get("/api/autofill/notices"); } catch { return; }
  for (const n of r.notices || []) {
    const t = toast(n.text, { ms: NOTICE_MS, extra: h("div", "row-btns",
      B({ class: "btn sm", onclick: () => { api("/api/autofill/notice/close", { id: n.id }).catch(() => {}); t.remove(); } }, "Got it"),
      B({ class: "btn sm ghost", onclick: () => { t.remove(); S.openSettings?.("autofill"); } }, "See Health")) });
  }
  const x = r.extension;
  if (!x || !x.newer) return;
  const key = `${SEEN_KEY}.${S.profile}.${x.current}`;
  try { if (localStorage.getItem(key)) return; } catch { /* storage blocked: say it each time */ }
  const t = toast(`Autofill ${x.current} is ready (yours is ${x.installed}). ${updateSteps()}`, { ms: NOTICE_MS,
    extra: B({ class: "btn sm", onclick: () => { try { localStorage.setItem(key, "1"); } catch { /* ok */ } t.remove(); } }, "Got it") });
}

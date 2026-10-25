// GOAL35 §1: the page state lives in the address, so reload, back / forward and links restore it.
import { JOB_TABS, S, TABS } from "./core.js";
import { sponsView } from "./sponsor.js";

export const FILTER_KEYS = ["sort", "posted", "family", "state", "workplace", "min_match", "salary", "board", "level", "size",
  "employment", "senior", "hide_applied", "dupes", "q"];
const FILTER_TABS = ["recommended", "saved", "archived"];
const RQ = ["queue", "ready", "done"];
const AP_SEGS = ["all", "waiting", "interviewing", "rejected"];
const TRACKER_STATUSES = ["saved", "applied", "screen", "interview", "offer", "rejected", "ghosted"];
const MAX_TEXT = 200;
const MAX_GF = 2000;       // the grid's column filters (JSON)

const clip = (v) => String(v || "").slice(0, MAX_TEXT);

// "#tracker?p=alex&q=x" (or old "#tracker:alex") → an object; unknown keys dropped
export function parseHash(hash = location.hash) {
  const raw = hash.replace(/^#/, "");
  const at = raw.indexOf("?");
  const path = at < 0 ? raw : raw.slice(0, at), query = at < 0 ? "" : raw.slice(at + 1);
  let tab = "", oldPid = "";
  try { [tab, oldPid = ""] = decodeURIComponent(path).split(":"); } catch { /* a broken address is the default page */ }
  const sp = new URLSearchParams(query);
  const filters = {};
  for (const k of FILTER_KEYS) if (sp.get(k)) filters[k] = clip(sp.get(k));
  return {
    tab: TABS.includes(tab) || tab === "settings" ? tab : "",
    profile: clip(sp.get("p") || oldPid),
    filters,
    rq: RQ.includes(sp.get("rq")) ? sp.get("rq") : "",
    spons: ["friendly", "no"].includes(sp.get("spons")) ? sp.get("spons") : "",
    seg: AP_SEGS.includes(sp.get("seg")) ? sp.get("seg") : "",
    status: TRACKER_STATUSES.includes(sp.get("status")) ? sp.get("status") : "",
    q: clip(sp.get("q")).trim(),
    wide: sp.get("wide") === "1",
    gsort: clip(sp.get("gsort")),
    gf: String(sp.get("gf") || "").slice(0, MAX_GF),
    sheet: clip(sp.get("sheet")),
    job: clip(sp.get("job")),
    section: clip(sp.get("section")),
  };
}

// the state shown now → "#tab?…" (keys in a fixed order, empty ones left out)
export function hashOf(state) {
  const sp = new URLSearchParams();
  if (state.profile) sp.set("p", state.profile);
  if (FILTER_TABS.includes(state.tab)) for (const k of FILTER_KEYS) if (state.filters?.[k]) sp.set(k, state.filters[k]);
  if (state.tab === "recommended" && state.spons) sp.set("spons", state.spons);
  if (state.tab === "resumes" && state.rq) sp.set("rq", state.rq);
  if (state.tab === "applied" && state.seg && state.seg !== "all") sp.set("seg", state.seg);
  if (state.tab === "tracker" && state.status) sp.set("status", state.status);
  if (["tracker", "interviewing"].includes(state.tab)) for (const k of ["sheet", "gsort", "gf"]) if (state[k]) sp.set(k, state[k]);
  if (state.q) sp.set("q", state.q);
  if (state.q && state.wide && ["tracker", "interviewing"].includes(state.tab)) sp.set("wide", "1");
  if (state.job && JOB_TABS.includes(state.tab)) sp.set("job", state.job);
  if (state.tab === "settings" && state.section) sp.set("section", state.section);
  const query = sp.toString();
  return `#${state.tab}${query ? `?${query}` : ""}`;
}

// the selected job; while the list loads, the one the address names
function jobNow() {
  if (!JOB_TABS.includes(S.tab)) return "";
  if (S.listTab === S.tab) return S.items[S.cur]?.uid || "";
  const was = parseHash();
  return S.place[S.tab]?.uid || (was.tab === S.tab ? was.job : "");
}

const searchOf = (tab) => (tab === "applied" ? S.apq : tab === "answers" ? S.afq : ["tracker", "interviewing"].includes(tab) ? S.T?.q : "");

export function currentState() {
  return { tab: S.tab, profile: S.profile, filters: S.filters, spons: sponsView(), rq: S.rq, seg: S.ap, status: S.T?.status, sheet: S.T?.sheetName,
    q: searchOf(S.tab), wide: S.T?.wide, gsort: S.T?.gsort, gf: S.T?.gf, job: jobNow(), section: S.section };
}

// write the address: a new history entry for a new page, else the same one rewritten
export function syncUrl({ push = false } = {}) {
  const next = hashOf(currentState());
  if (next === location.hash) return;
  if (push) history.pushState(null, "", next);
  else history.replaceState(history.state, "", next);
}

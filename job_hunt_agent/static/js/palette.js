// ⌘K palette: pages, people, then (GOAL35 §5.2) every application, company and job of the person from the server's
// search (/api/search: any status, any age, typos, aliases), not only the jobs drawn on the page. Lazy.
import { $, S, STATUS_LABELS, TABS, closePop, get, h, shortDate, sm, tabName } from "./core.js";
import { openJob } from "./detail.js";
import { openRowEditor } from "./editor.js";
import { setFilters } from "./filters.js";
import { marked, match } from "./find.js";

const PALETTE_MAX = 40;
const JOB_LISTS = ["recommended", "saved", "archived"];
const ASK_MS = 180;
let app = {};
let palItems = [];
let palCur = 0;
let wired = false;
let remote = { q: "", data: null };
let askTimer = 0, askToken = 0;

function localEntries() {
  const tabs = TABS.map((t, i) => ({ kind: "Page", label: tabName(t), hint: String(i + 1), run: () => app.switchTab(t) }));
  const people = S.profiles.filter((p) => p.id !== S.profile)
    .map((p) => ({ kind: "Profile", label: `Switch to ${p.name}`, run: () => app.setProfile(p.id) }));
  return [...tabs, ...people, { kind: "Page", label: "Settings", run: () => app.openSettings() }];
}

// the Tracker opened on this search (every status)
function trackerSearch(q) {
  const want = { q, status: "", sheetName: "", wide: false };
  S.urlT = want;
  if (S.T) Object.assign(S.T, want);
  app.switchTab("tracker");
}

function feedSearch(q) {
  app.switchTab("recommended");
  setFilters({ ...S.filters, q });
}

const foundIn = (r) => (r.where?.length && !r.where.some((w) => ["company", "title"].includes(w)) ? ` · found in ${r.where.join(", ")}` : "");

function remoteEntries(d) {
  if (!d) return [];
  const a = d.applications || { rows: [], total: 0 };
  const apps = [
    ...a.rows.map((r) => ({ kind: STATUS_LABELS[r.status] || r.status, label: `${r.company || "(company)"} · ${r.role_title || "(role)"}`,
      sub: `${r.applied_on ? `applied ${shortDate(r.applied_on)}` : "not applied"}${foundIn(r)}`, run: () => openRowEditor(r.id) })),
    a.total ? { kind: "Tracker", label: `All ${a.total} application${a.total === 1 ? "" : "s"} matching “${d.q}”`, run: () => trackerSearch(d.q) } : null];
  const jobs = [
    ...(d.jobs || []).map((j) => ({ kind: "Job", label: `${j.company} · ${j.title}`, sub: j.location || "", run: () => openJob(j.uid, { sheet: true }) })),
    d.jobs_total ? { kind: "Jobs", label: `All ${d.jobs_total} job${d.jobs_total === 1 ? "" : "s"} matching “${d.q}”`, run: () => feedSearch(d.q) } : null];
  const cos = (d.companies || []).map((c) => ({ kind: "Company", label: c.company, sub: c.ats || "", run: () => feedSearch(c.company) }));
  // 2026-10-24: on a job list its open jobs come first (the user searched "amazon" on Recommended to find new Amazon jobs)
  if (!JOB_LISTS.includes(S.tab)) return [...apps, ...jobs, ...cos].filter(Boolean);
  const name = d.no_jobs_at;
  if (!name) return [...jobs, ...apps, ...cos].filter(Boolean);
  const same = (c) => c.label.trim().toLowerCase() === name.trim().toLowerCase();
  const none = { kind: "Jobs", icon: "ban", label: `No open ${name} jobs in your list right now`, run: () => feedSearch(name) };
  return [none, ...cos.filter(same), ...jobs, ...apps, ...cos.filter((c) => !same(c))].filter(Boolean);
}

function paintPalette() {
  const q = $("#pal-q").value.trim();
  const local = localEntries().filter((x) => !q || match(q, x.label));
  const got = remote.q === q ? remote.data : null;
  palItems = [...local, ...remoteEntries(got)].slice(0, PALETTE_MAX);
  palCur = Math.min(palCur, Math.max(0, palItems.length - 1));
  const terms = [...(got?.applications?.terms || []), ...(got?.terms || [])];
  $("#pal-list").replaceChildren(...(palItems.length ? palItems.map((x, i) => h("li", { role: "option", id: `pal-${i}`,
    "aria-selected": String(i === palCur), title: `${x.kind}: ${x.label}${x.hint ? ` (${x.hint})` : ""}`, onclick: () => runPalette(i) },
  h("span", "pal-kind", x.icon ? [sm(x.icon), " "] : null, x.kind), h("span", "pal-label", ...marked(x.label, terms)), x.sub ? h("span", "muted small", x.sub) : null,
  x.hint ? h("kbd", {}, x.hint) : null))
    : [h("li", "pal-empty", q && remote.q !== q ? "Searching…"
      : `Nothing matches “${q}”. Searched pages, every application (any status and age, with its JD), companies and jobs.`)]));
  $("#pal-q").setAttribute("aria-activedescendant", palItems.length ? `pal-${palCur}` : "");
  $(`#pal-${palCur}`)?.scrollIntoView({ block: "nearest" });
}

function ask() {
  const q = $("#pal-q").value.trim();
  clearTimeout(askTimer);
  const mine = ++askToken;
  if (!q) { remote = { q: "", data: null }; return; }
  askTimer = setTimeout(() => get("/api/search", { q }).then((data) => {
    if (mine !== askToken) return;
    remote = { q, data };
    paintPalette();
  }, () => { if (mine === askToken) { remote = { q, data: null }; paintPalette(); } }), ASK_MS);
}

function runPalette(i) {
  const item = palItems[i];
  $("#palette").close();
  item?.run();
}

function wirePalette() {
  const input = $("#pal-q");
  input.addEventListener("input", () => { palCur = 0; ask(); paintPalette(); });
  input.addEventListener("keydown", (e) => {
    const moves = { ArrowDown: 1, ArrowUp: -1 };
    if (moves[e.key]) {
      e.preventDefault();
      palCur = (palCur + moves[e.key] + palItems.length) % Math.max(1, palItems.length);
      paintPalette();
    } else if (e.key === "Enter") { e.preventDefault(); runPalette(palCur); }
  });
  $("#palette").addEventListener("click", (e) => { if (e.target.id === "palette") $("#palette").close(); });
  wired = true;
}

export function openPalette(given) {
  app = given;
  if (!wired) wirePalette();
  const dlg = $("#palette");
  if (dlg.open) return;
  closePop();
  $("#pal-q").value = "";
  remote = { q: "", data: null };
  palCur = 0;
  paintPalette();
  dlg.showModal();
  $("#pal-q").focus();
}

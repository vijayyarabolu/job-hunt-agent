// Job list: rows, selection, actions + Undo, "N new" pill.
import { OPEN_TIP, $, $$, B, btn, FAMILY_LABELS, S, WORTH_TIP, api, calm, copyText, fail, get, h, icon, loadError, locSpan, muted, postedText,
  openExt, optimistic, phone, refreshFailed, shortDate, shortLoc, store, tabName, toast } from "./core.js";
import { miniRing, tailoredLine } from "./ring.js";
import { closeDetail, openJob, refreshDetail, reloadDetail } from "./detail.js";
import { filterParams, setBoards, setFilters } from "./filters.js";
import { REMOVE_UNDO_MS, openApplyDialog, removeApp, xBtn } from "./dialogs.js";
import { openRowEditor } from "./editor.js";
import { sponsSwitch, sponsView, sponsorBadge } from "./sponsor.js";

const REASONS = [["too_senior", "Too senior"], ["wrong_role", "Wrong role"], ["location", "Location"], ["company", "Company"]];
const SKELETON_ROWS = 9;
let loadToken = 0;
let fresh = [];
let meta = {};

export const cur = () => S.items[S.cur];
const applied = (job) => job.app && !["saved", "tailored"].includes(job.app.status);
const isSaved = (job) => job.triage === "saved" || (job.app && ["saved", "tailored"].includes(job.app.status));

function skeleton() {
  const row = () => h("li", { class: "row sk", "aria-hidden": "true" }, h("span", "sk-ring"),
    h("span", "row-main", h("span", "sk-line w70"), h("span", "sk-line w40")));
  $("#list").replaceChildren(...Array.from({ length: SKELETON_ROWS }, row));
  $("#list-more").replaceChildren();
  $("#list").setAttribute("aria-busy", "true");
  const count = $("#lh-title .muted"), name = $("#lh-title")?.firstChild;
  if (name?.nodeType === 3) name.textContent = `${tabName(S.tab)} `;   // GOAL30-app: the new tab's name at once
  if (count) count.textContent = "updating…";
  if (S.tab !== "recommended") $$("#list-head .lh-hold").forEach((el) => el.remove());
}

const boards = {}; // b: last Resumes boards, shown at once
function show(data, back) {
  meta = data;
  setBoards(data.boards);
  fresh = [];
  if (data.progress) S.prog = data.progress;
  S.items = data.jobs || [];
  const at = back?.uid ? S.items.findIndex((j) => j.uid === back.uid) : -1;
  S.cur = at >= 0 ? at : Math.min(S.cur, Math.max(0, S.items.length - 1));
  renderAll();
  if (back) { $("#list-wrap").scrollTop = back.top; paintSel(); }
  if (!phone.matches && S.items.length && !S.detail?.panel) openCard(cur(), { quiet: true });
  if (!S.items.length) closeDetail();
}

// GOAL31 §3: each list's last data, shown at once on the way back while it refreshes; nothing new redraws nothing
const LAST_MAX = 16;
const last = new Map();
let shown = { memo: "", sig: "" };
const remember = (memo, data) => { last.delete(memo); last.set(memo, data); if (last.size > LAST_MAX) last.delete(last.keys().next().value); };
// the list as it is now (saved, removed since), for the way back
export function keepList() { if (shown.memo) remember(shown.memo, { ...meta, jobs: S.items }); }

export async function loadFeed({ quiet = false } = {}) {
  const token = ++loadToken;
  const tab = S.tab;
  const key = tab === "resumes" && `${S.profile} ${S.rq}`;
  const back = S.place[tab] || (quiet && { uid: cur()?.uid, top: $("#list-wrap").scrollTop });
  S.place[tab] = null;
  if (tab === "applied" && !S.apMod) {
    if (!quiet) skeleton();
    S.apMod = await import("./applied.js");
    if (token !== loadToken) return;
  }
  const params = key ? { tab, rq: S.rq } : tab === "applied" ? S.apMod.params() : { tab, ...filterParams() };
  const memo = `${S.profile}|${JSON.stringify(params)}`;
  const hit = !quiet && (last.get(memo) || (key && (boards[key] ||= store.json(`jh.rq.${key}`, null))));
  if (hit) { show(hit, back); shown = { memo, sig: JSON.stringify(hit) }; $("#list").setAttribute("aria-busy", "true"); } else if (!quiet) skeleton();
  try {
    const data = await get("/api/jobs", params);
    if (token !== loadToken || tab !== S.tab) return;
    remember(memo, data);
    if (key) store.set(`jh.rq.${key}`, JSON.stringify(boards[key] = data));
    const sig = JSON.stringify(data);
    if (shown.memo === memo && shown.sig === sig) return;
    shown = { memo, sig };
    show(data, hit || quiet ? { uid: cur()?.uid, top: $("#list-wrap").scrollTop } : back);
  } catch (err) {
    if (token !== loadToken) return;
    if ((hit || quiet) && S.items.length) refreshFailed(err, () => loadFeed({ quiet: true }));
    else { $("#list").replaceChildren(); $("#list-more").replaceChildren(loadError(err, () => loadFeed())); }
  } finally {
    if (token === loadToken) $("#list").removeAttribute("aria-busy");
  }
}

function renderAll() {
  renderHead();
  renderList();
  renderFoot();
}

function renderHead() {
  if (S.tab === "resumes") { $("#list-head").replaceChildren(...(S.g8?.head(meta) || [h("h1", { id: "lh-title" }, "Resumes")]).filter(Boolean)); return; }
  if (S.tab === "applied") { S.apMod?.head(meta, loadFeed); return; }
  const t = meta.today;
  const parts = t ? Object.entries(t.by_family).map(([f, n]) => `${FAMILY_LABELS[f] || f} ${n}`) : [];
  const pill = fresh.length ? B({ class: "pill-new", title: "Show them at the top of the list", onclick: insertFresh }, `${fresh.length} new job${fresh.length === 1 ? "" : "s"}`) : null;
  const extra = S.tab === "saved" ? meta.outside?.length || 0 : 0;   // GOAL33: Saved counts its outside jobs
  const total = (meta.total ?? S.items.length) + extra, n = S.items.length + extra;
  const shown = n < total ? `${n} of ${total}` : String(total);
  $("#list-head").replaceChildren(...[h("div", "lh-top", h("h1", { id: "lh-title" }, tabName(S.tab), " ",
    muted(`${shown} · ${S.filters.sort === "match" ? "best match first" : "newest first"}`)), pill),
  S.tab === "recommended" && t ? h("p", "daily", h("b", {}, `${t.total} new in the last 24 h`),
    muted(parts.length ? ` · ${parts.join(" · ")}` : ""), t.total ? bestBtn() : null, prog()) : null,
  S.tab === "recommended" ? sponsSwitch(meta.spons, sponsView(), () => loadFeed()) : null,
  dupesBtn(),
  meta.min_match_floor ? h("p", "floor muted", `Showing ${meta.min_match_floor}%+ matches (your floor)`) : null].filter(Boolean));
  S.g8?.paintHead();
}

// W7 / W9: one click to the best new jobs and to the duplicates
const bestBtn = () => [muted(" · "), B({ class: "lnk", title: "Posted in the last 24 h, best match first",
  onclick: () => setFilters({ ...S.filters, posted: "24h", sort: "match" }) }, "Show the best first")];
function dupesBtn() {
  const n = meta.dupes;
  if (!n && !S.filters.dupes) return null;
  return h("p", "lh-dupes small", S.filters.dupes ? B({ class: "lnk", title: "Back to every job", onclick: () => setFilters({ ...S.filters, dupes: "" }) }, "Show every job")
    : B({ class: "lnk", title: "Only the older copies of a job posted twice; × removes a copy",
      onclick: () => setFilters({ ...S.filters, dupes: "1" }) }, `${n} duplicate${n === 1 ? "" : "s"}: show`));
}

export const prog = (lead = " · ", p = S.prog) => (p?.goal ? h("span", { class: "prog muted", title: "Applied today; your daily goal is in Settings" },
  `${lead}Applied today `, h("b", {}, String(p.applied_today)), ` · goal ${p.goal}`) : null);

function renderFoot() {
  const bits = [meta.filtered ? `${meta.filtered} hidden by dealbreakers (security clearance)` : "",
    meta.jd_gate ? `${meta.jd_gate} hidden: no GenAI work in the JD` : "",
    meta.too_senior && !S.filters.senior ? `${meta.too_senior} too senior (More → Show too senior)` : ""];
  $("#foot").textContent = bits.filter(Boolean).join(" · ");
}

function tags(job) {
  const lab = job.labels || {};
  return [applied(job) && !job.ap ? h("span", { class: "tag", title: "Applied: on your tracker (m)" }, "Applied")
    : isSaved(job) ? h("span", { class: "tag soft", title: "Saved: in your tracker (s to unsave)" }, "Saved") : null,
    lab.applied_before && !applied(job) ? h("span", { class: "tag warn", title: `${lab.applied_before}: this role at this company is on your tracker` }, "Applied before") : null,
    lab.too_senior ? h("span", { class: "tag warn", title: "Too senior: asks more years than your cap" }, "Too senior") : null,
    job.ghost ? h("span", { class: "tag warn", title: "Flagged, never hidden: Priority a few points lower" }, icon("ghost", "ic sm"), job.ghost) : null,
    h("span", { class: "tag soft yrs", title: "Years of experience read from the JD" }, job.asks_years == null ? "years not stated" : `Asks ${job.asks_years} yr${job.asks_years === 1 ? "" : "s"}`)];
}
function heart() {
  const ic = icon("heart", "ic sm liked");
  ic.setAttribute("title", "Liked: similar roles rank higher (l to unlike)");
  return ic;
}

export const RG_ON_KEY = "jh.rg.on";
function chipSlot(uid) {
  const on = store.get(RG_ON_KEY, "1") === "1";
  return on ? h("span", { class: "rgc rg-gen", "data-uid": uid, title: "Generate a tailored resume (g)", "aria-hidden": "true" }, "Generate resume")
    : h("span", { class: "rgc", "data-uid": uid });
}
function rowEl(job, i) {
  const title = h("div", { class: "row-title", title: job.title }, job.liked ? heart() : null, h("span", {}, job.title));
  return h("li", { role: "option", id: `job-${i}`, class: `row${job.triage === "dismissed" && S.tab !== "archived" ? " dim" : ""}`,
    "aria-selected": String(i === S.cur), "data-i": i, "aria-checked": S.rg?.on ? String(S.rg.picks.has(job.uid)) : null,
    title: "Open (Enter) · o opens the posting",
    onclick: (e) => { if (e.shiftKey && S.g8) return S.g8.pick(i, e); select(i, { open: false }); openCard(job, { sheet: true }); } },
  h("span", { class: "pick", "aria-hidden": "true", onclick: (e) => { e.stopPropagation(); S.g8?.pick(i, e); } }),
  miniRing(job.match, "", tailoredLine(job), job.no_jd ? "nojd" : job.scoring ? "scoring" : null),
  h("div", "row-main", title, h("div", "row-meta", h("span", "co", job.company), sponsorBadge(job.sponsor), job.location ? locSpan(job.location, "loc") : null, tags(job),
    job.tailored ? h("span", { class: "tag soft", title: tailoredLine(job) }, `${job.match_base}% → ${job.match}% tailored`) : null,
    also(job), job.pass_reason ? h("span", { class: "tag soft", title: "Why you passed on it" }, job.pass_reason) : null, dupTag(job), job.outside || job.ap ? null : chipSlot(job.uid)), S.g8?.row(job)),
  job.ap ? S.apMod.side(job) : h("div", "row-side", job.outside ? null : xBtn(`${job.company} · ${job.title}`, () => removeJob(job)),
    job.posted_ts ? h("time", { class: job.posted_found ? "found" : null, datetime: job.posted_ts, "data-ts": job.posted_ts, "data-exact": job.posted_exact ? "1" : "",
      "data-found": job.posted_found || "", "data-day": job.posted_day || "",
      title: `${job.posted_label} ${new Date(job.posted_ts).toLocaleString()}${job.posted_exact ? "" : " (day only)"}` },
    postedText(job.posted_ts, job.posted_exact, job.posted_found, job.posted_day)) : null,
    job.outside ? h("span", { class: "tag soft", title: "A JD pasted in the Resume app: its match comes from that JD" }, "outside job")
      : h("span", { class: `worth${job.worth >= 70 ? " w-hi" : ""}`, title: WORTH_TIP },
        job.worth == null ? "scoring" : ["Priority ", h("b", {}, job.worth)])));
}

const dupTag = (job) => (job.dup_of ? h("span", { class: "tag warn dup-tag", title: `The same posting as the newer ${job.dup_of.label} (same company, role and place, or the same posting link): × removes this copy` },
  `Duplicate of ${job.dup_of.label}`) : null);
const also = (job) => (job.also?.length ? h("span", { class: "tag soft", title: job.also.map((a) => a.location).join("\n") },
  `+${job.also.length} location${job.also.length === 1 ? "" : "s"}`) : null);
export const openCard = (job, opts = {}) => (!job?.outside ? openJob(job.uid, opts)
  : opts.quiet ? closeDetail() : openRowEditor(job.outside));

function archivedApps() {
  const apps = meta.applications || [];
  if (S.tab !== "archived" || !apps.length) return [];
  return [h("div", "section-head", h("h2", {}, `Rejected / cold applications (${apps.length})`)),
    h("ul", "apps", apps.map((a) => h("li", {}, B({ class: "row app-row", title: OPEN_TIP, onclick: () => openRowEditor(a.id) },
      h("span", "ring-blank"), h("span", "row-main", h("span", "row-title", h("span", {}, a.role_title || "(role)")),
        h("span", "row-meta", h("span", "co", a.company || "(company)"),
          muted(a.applied_on ? `applied ${shortDate(a.applied_on)}` : "not applied yet"),
          a.address ? h("span", "loc", shortLoc(a.address)) : null)),
      h("span", "row-side", h("span", `st st-${a.status}`, a.status))),
      xBtn(`${a.company} · ${a.role_title}`, () => removeApp(a, () => loadFeed({ quiet: true }), () => loadFeed({ quiet: true }))))))];
}

export function rescore(scores = {}) {
  S.items = S.items.map((job, i) => {
    const t = scores[job.uid], base = job.tailored ? job.match_base : job.match;
    if (t ? job.tailored?.pct === t.pct : !job.tailored) return job;
    const next = t ? { ...job, match: t.pct, match_base: base, tailored: { pct: t.pct, base } }
      : { ...job, match: base, match_base: undefined, tailored: undefined };
    $(`#list .row[data-i="${i}"]`)?.replaceWith(rowEl(next, i));
    return next;
  });
}

function renderList() {
  const empty = S.items.length || (S.tab === "saved" && meta.outside?.length) ? null : h("p", "empty", S.tab === "saved" ? "Nothing saved yet: press s on a job to keep it here."
    : S.tab === "resumes" ? S.g8?.rqEmpty(meta) : S.tab === "applied" ? S.apMod.empty() : Object.keys(S.filters || {}).some((k) => k !== "sort")
      ? ["Nothing here with these filters. ", B({ class: "linklike", title: "Remove every filter", onclick: () => setFilters({ sort: S.filters.sort }, "Cleared all filters") }, "Clear filters")]
      : S.tab === "archived" ? "Nothing archived yet: jobs you mark Not interested come here." : "Nothing here yet: new jobs come in with each fetch.");
  const dayOf = (j) => (j?.rq || j?.ap)?.day;   // GOAL18 §3
  $("#list").replaceChildren(...S.items.flatMap((j, i) => (dayOf(j) && dayOf(j) !== dayOf(S.items[i - 1])
    ? [h("li", { class: "section-head", role: "presentation" }, h("h2", {}, dayOf(j))), rowEl(j, i)] : rowEl(j, i))));
  const more = !meta.counts && S.items.length < (meta.total ?? 0)
    ? btn("more-btn", "Load the next jobs (or keep scrolling)", moreJobs, `Load more (${S.items.length.toLocaleString()} of ${meta.total.toLocaleString()})`) : "";
  $("#list-more").replaceChildren(...(empty && !meta.outside?.length ? [empty] : []), more, ...archivedApps(), ...(S.g8?.outside(meta) || []));
  paintSel();
  S.g8?.paint();
  if (more) watchMore(more);
}

// "Load more" loads itself when seen
let moreSeen = null, loadingMore = false;
function watchMore(el) {
  moreSeen?.disconnect();
  if (!("IntersectionObserver" in window)) return;
  moreSeen = new IntersectionObserver((seen) => { if (seen.some((e) => e.isIntersecting)) moreJobs(); }, { root: $("#list-wrap"), rootMargin: "400px" });
  moreSeen.observe(el);
}

async function moreJobs() {
  if (loadingMore) return;
  loadingMore = true;
  const tab = S.tab, token = loadToken;
  try {
    const data = await get("/api/jobs", { tab, ...filterParams(), offset: S.items.length });
    if (tab !== S.tab || token !== loadToken) return;
    const have = new Set(S.items.map((j) => j.uid));
    S.items = [...S.items, ...data.jobs.filter((j) => !have.has(j.uid))];
    meta = { ...data, jobs: undefined };
    const top = $("#list-wrap").scrollTop;
    renderAll();
    $("#list-wrap").scrollTop = top;
  } catch (err) { fail(err); } finally { loadingMore = false; }
}

function paintSel() {
  const rows = [...$("#list").querySelectorAll(".row[data-i]")];
  rows.forEach((r) => r.setAttribute("aria-selected", String(Number(r.dataset.i) === S.cur)));
  const row = rows.find((r) => Number(r.dataset.i) === S.cur);
  const bar = $("#sel-bar");
  bar.hidden = !row;
  if (!row) return;
  $("#list").setAttribute("aria-activedescendant", row.id);
  bar.style.transform = `translateY(${row.offsetTop}px)`;
  bar.style.height = `${row.offsetHeight}px`;
  const wrap = $("#list-wrap"), y = row.offsetTop - wrap.scrollTop;
  if (y < 0 || y + row.offsetHeight > wrap.clientHeight) row.scrollIntoView({ block: "nearest" });
}
addEventListener("resize", () => requestAnimationFrame(paintSel));

export function select(i, { open = !phone.matches } = {}) {
  if (!S.items.length) return;
  S.cur = Math.max(0, Math.min(S.items.length - 1, i));
  paintSel();
  if (open) openCard(cur(), { quiet: true });
}

function redraw(job) {
  const i = S.items.indexOf(job);
  const old = $(`#job-${i}`);
  if (old) old.replaceWith(rowEl(job, i));
  paintSel();
  refreshDetail(job.uid);
}
function drop(job) {
  const i = S.items.indexOf(job);
  if (i < 0) return () => {};
  const row = $(`#job-${i}`);
  const count = (n) => { if (meta.total != null) meta = { ...meta, total: meta.total + n }; };
  let timer = 0;
  const take = () => { timer = 0; S.items = S.items.filter((j) => j !== job); count(-1); renderAll(); select(Math.min(i, S.items.length - 1)); };
  if (row && !calm.matches) { row.classList.add("leave"); timer = setTimeout(take, 160); } else take();
  return () => {
    if (timer) { clearTimeout(timer); timer = 0; row.classList.remove("leave"); return; }
    S.items = [...S.items.slice(0, i), job, ...S.items.slice(i)]; count(1); renderAll(); select(i);
  };
}

// one job's writes go out in click order
const inFlight = new Map();
function inOrder(uid, send) {
  const run = (inFlight.get(uid) || Promise.resolve()).then(send);
  const done = run.catch(() => {});
  inFlight.set(uid, done);
  done.then(() => { if (inFlight.get(uid) === done) inFlight.delete(uid); });
  return run;
}
const feedback = (job, kind, reason, note) => inOrder(job.uid, () => api("/api/feedback", { uid: job.uid, kind, reason, note }));
const triage = (job, state) => inOrder(job.uid, () => api("/api/triage", { uid: job.uid, state }));
const leavesTab = (job, saved) => (S.tab === "saved" && !saved) || (S.tab === "archived");

export function save(job) {
  if (!job) return;
  const was = job.triage;
  const next = was === "saved" ? "restored" : "saved";
  let putBack = () => {};
  optimistic(() => { job.triage = next; if (leavesTab(job, next === "saved")) putBack = drop(job); else redraw(job); },
    () => { job.triage = was; putBack(); redraw(job); },
    () => triage(job, next),
    next === "saved" ? `Saved: ${job.company}` : `Removed from Saved: ${job.company}`,
    () => { job.triage = was; putBack(); redraw(job); triage(job, was || "restored").catch(fail); });
}

export function like(job) {
  if (!job) return;
  const was = { liked: job.liked, triage: job.triage };
  const back = async () => {
    await feedback(job, "clear");
    if (was.triage === "saved") await triage(job, "saved");
  };
  const restore = () => { Object.assign(job, was); redraw(job); };
  optimistic(() => { job.liked = !was.liked; if (!was.liked) job.triage = "saved"; redraw(job); }, restore,
    () => (was.liked ? back() : feedback(job, "like")),
    was.liked ? "Like removed" : `Liked: saved, and similar ${job.company} roles rank a little higher`,
    () => { restore(); (was.liked ? feedback(job, "like") : back()).catch(fail); });
}

let lastWhy = null;
export function notInterested(job) {
  if (!job) return;
  if (job.outside) { toast("An outside job: remove it in the Resume app"); return; }
  const was = { triage: job.triage, liked: job.liked };
  let reason = null;
  const pick = (key, text, note) => feedback(job, "pass", key, note).then(() => {
    reason = key;
    job.pass_reason = note ? `Other: ${note}` : text;
    why.replaceChildren(note ? `Reason saved: ${note}` : `Reason: ${text.toLowerCase()}. You will see fewer like it.`);
  }, fail);
  const chips = REASONS.map(([key, text]) => B({ class: "chip", title: `${text}: you will see fewer like it`, onclick: () => pick(key, text) }, text));
  const other = () => {
    const box = h("input", { type: "text", maxlength: "200", placeholder: phone.matches ? "Why? Type it, then Go" : "Why? (Enter saves, Esc closes)", "aria-label": "Other reason", title: "Type the reason: Enter saves, Esc closes" });
    box.onkeydown = (e) => {
      e.stopPropagation();
      if (e.key === "Escape") { e.preventDefault(); why.replaceChildren(...chips); chips[4].focus(); }
      if (e.key === "Enter" && box.value.trim()) { e.preventDefault(); box.disabled = true; pick("other", "Other", box.value.trim()).finally(() => { box.disabled = false; }); }
    };
    why.replaceChildren(box);
    box.focus();
  };
  chips.push(B({ class: "chip", title: "Type your own reason", "data-k": "other", onclick: other }, "Other…"));
  const why = h("div", { class: "why", role: "group", "aria-label": "Why not interested?" }, chips);
  job.triage = "dismissed";
  const putBack = S.tab === "archived" ? () => {} : drop(job);
  const undo = async () => {
    Object.assign(job, was);
    putBack();
    try {
      if (reason) await feedback(job, was.liked ? "like" : "clear");
      if (job.triage !== was.triage) return;
      await triage(job, was.triage || "restored");
    } catch (err) { fail(err); }
  };
  triage(job, "dismissed")
    .then((r) => {
      // W11: one "Why?" at a time, naming its job: an older toast keeps its Undo, its reasons go
      if (lastWhy?.isConnected && !lastWhy.contains(document.activeElement)) lastWhy.replaceChildren(muted("No reason given", "muted small"));
      lastWhy = why;
      toast(`Not interested: ${job.company} · ${job.title}${r?.resumes?.length ? " · its resume archived in the Resume app" : ""}. Why?`, { extra: why, undo });
    })
    .catch((err) => { Object.assign(job, was); putBack(); toast(`Not saved: ${err.message}. Put back as it was.`, { error: true }); });
}

// × / Shift+X: out of every list for good, never back from a fetch
export function removeJob(job) {
  if (!job) return;
  if (job.outside) { toast("An outside job: remove it in the Resume app"); return; }
  const putBack = drop(job);
  api("/api/remove", { uid: job.uid, why: job.dup_of ? "duplicate" : "unwanted" })
    .then((r) => toast(`Removed: ${job.company} · ${job.title}${r.resumes?.length ? " · its resume archived in the Resume app" : ""}`, { ms: REMOVE_UNDO_MS,
      undo: () => api("/api/remove/undo", { id: r.removed }).then(() => putBack(), fail) }))
    .catch((err) => { putBack(); toast(`Not removed: ${err.message}. Put back as it was.`, { error: true }); });
}

export function restore(job) {
  if (!job) return;
  let putBack = () => {};
  optimistic(() => { putBack = drop(job); }, () => putBack(),
    () => feedback(job, "clear"), `Restored to the feed: ${job.company}`,
    () => { putBack(); feedback(job, "pass").catch(fail); });
}

export function apply(job) {
  if (!job) return;
  S.g8?.inHand(job);
  openExt(job.apply_url || job.url);
  api("/api/apply_click", job.outside ? { app: job.outside } : { uid: job.uid }).then((r) => {
    if (!r?.app || job.app) return;
    job.app = { id: r.app, status: "saved" };
    redraw(job);
    reloadDetail(job.uid);
  }).catch(fail);
}

export function markApplied(job, resume = "") {
  if (!job) return;
  if (job.outside) { S.g8?.confirmOutside({ id: job.outside, company: job.company }).then(() => loadFeed({ quiet: true })); return; }
  openApplyDialog({ uid: job.uid, resume, onDone: (appId, withFile) => {
    const was = job.app;
    job.app = { ...(job.app || {}), id: appId, status: "applied" };
    S.askIn?.delete(job.uid);              // answers its "Applied?" line
    if (S.tab === "saved" || S.tab === "resumes") drop(job); else redraw(job);
    reloadDetail(job.uid);
    const missing = withFile ? "" : " Resume missing: upload it from the tracker row any time.";
    toast(`Marked applied: ${job.company}. Syncing to your Sheet.${missing}`, { undo: async () => {
      try { await api("/api/tracker/undo", { app: appId }); job.app = was; redraw(job); reloadDetail(job.uid); toast("Back as it was"); }
      catch (err) { fail(err); }
    } });
  } });
}

export async function copyJd(uid, keywords = false, resume = "") {
  try {
    const d = await get("/api/jd_text", { uid, keywords: keywords ? "1" : "", resume });
    const used = keywords && d.resume ? ` (${(d.resumes.find((r) => r.id === d.resume) || {}).label} resume)` : "";
    copyText(d.text, `Copied ${keywords ? `JD + keywords${used}` : "JD"}: ${d.company} · ${d.title}`);
  } catch (err) { fail(err); }
}

export async function checkNew() {
  if (S.tab !== "recommended" || document.hidden) return;
  try {
    const data = await get("/api/jobs", { tab: S.tab, ...filterParams() });
    const seen = new Set(S.items.map((j) => j.uid));
    fresh = data.jobs.filter((j) => !seen.has(j.uid));
    meta = { ...data, jobs: undefined };
    if (fresh.length) { meta.pending = data.jobs; renderHead(); }
  } catch { /* the next run tries again */ }
}
function insertFresh() {
  const uids = new Set(fresh.map((j) => j.uid));
  const keep = cur()?.uid;
  S.items = meta.pending || [...fresh, ...S.items];
  fresh = [];
  S.cur = Math.max(0, S.items.findIndex((j) => j.uid === keep));
  renderAll();
  for (const row of $("#list").querySelectorAll(".row[data-i]")) {
    if (uids.has(S.items[Number(row.dataset.i)]?.uid)) row.classList.add("enter");
  }
  $("#list-wrap").scrollTo({ top: 0, behavior: calm.matches ? "auto" : "smooth" });
}

export function tickAges() {
  if (document.hidden) return;
  for (const t of $("#list").querySelectorAll("time[data-ts]")) t.textContent = postedText(t.dataset.ts, t.dataset.exact === "1", t.dataset.found, t.dataset.day);
}


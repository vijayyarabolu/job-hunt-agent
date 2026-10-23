// Detail pane (phone: a sheet): a job or a panel.
import { $, B, btn, FIND_TIP, JOB_TABS, S, WORTH_TIP, api, calm, closePop, exactAge, postedText, extLink, fail, fullJd, get, h, icon, iconBtn, locSpan, menu, muted, openExt,
  openPop, opt, phone, sm, toast, still } from "./core.js";
import { bigRing, quadGlyph, tailoredLine } from "./ring.js";
import { apply, copyJd, like, markApplied, notInterested, removeJob, restore, save } from "./feed.js";
import { openAddJob, openRowEditor } from "./editor.js";
import { REMOVE_TIP, resumeBtns } from "./dialogs.js";
import { sponsorBadge, sponsorBlock } from "./sponsor.js";
const COPY_TIP = "Copy the job description (C)", POST_TIP = "Open the original posting in a new tab (o)";

const SWIPE_CLOSE_PX = 110;
const cache = new Map();
const pane = () => $("#detail");
let token = 0;

const findJob = (uid) => S.items.find((j) => j.uid === uid) || cache.get(uid)?.card;
const item = (text, title, onclick) => Object.assign(opt(text, onclick), { title });

// GOAL33 §3d: the job open in this tab, so a reload comes back to it (sessionStorage: this tab only, gone on close)
const OPEN_KEY = "jh.open";
const keepOpen = (v) => { try { if (v) sessionStorage.setItem(OPEN_KEY, JSON.stringify(v)); else sessionStorage.removeItem(OPEN_KEY); } catch { /* private mode */ } };
export function openOnReload() { try { return JSON.parse(sessionStorage.getItem(OPEN_KEY) || "null"); } catch { return null; } }

export async function openJob(uid, { sheet = false, quiet = false } = {}) {
  const sameJob = S.detail?.uid === uid;
  keepOpen({ tab: S.tab, uid });
  S.detail = { uid, resume: sameJob ? S.detail.resume : null };
  const job = findJob(uid);
  if (job) render(job, cache.get(uid), !quiet && !sameJob);
  else pane().replaceChildren(h("div", "d-in", h("p", "muted", "Loading…")));
  if (sheet) openSheet();
  if (cache.has(uid)) return;
  const mine = ++token;
  try {
    const d = await get("/api/job", { uid });
    cache.set(uid, d);
    if (mine === token && S.detail?.uid === uid) render(findJob(uid) || d.card || d, d, false);
  } catch (err) { if (mine === token) fail(err); }
}

export function refreshDetail(uid) {
  if (S.detail?.uid !== uid) return;
  const top = pane().scrollTop;
  render(findJob(uid), cache.get(uid), false);
  pane().scrollTop = top;
}

export function closeDetail() {
  S.detail = null;
  keepOpen(null);
  panelClose = null;
  pane().replaceChildren();
  closeSheet();
}

let panelClose = null;
export function showPanel(nodes, { keep = false, onClose = null, opener = null, close = "Close (Esc)" } = {}) {
  const top = pane().scrollTop;
  if (!keep || onClose) panelClose = onClose;
  const prev = S.detail || {};
  S.detail = keep && prev.panel ? prev : { panel: true, opener: opener || document.activeElement, from: prev.panel ? prev.from : prev.uid };
  pane().replaceChildren(h("div", "d-in panel", grab(), iconBtn(close, "x", closePanel, { class: "icon-btn close" }), ...nodes.filter(Boolean)));
  document.body.classList.add("panel-open");
  openSheet();
  if (keep) pane().scrollTop = top;
  else still(pane());
}
export function closePanel() {
  const done = panelClose;
  const { opener, from } = S.detail || {};
  document.body.classList.remove("panel-open");
  closeDetail();
  done?.();
  if (phone.matches && from && findJob(from)) { openJob(from, { sheet: true, quiet: true }); return; }
  if (JOB_TABS.includes(S.tab) && S.items[S.cur] && !phone.matches && !S.items[S.cur].outside) openJob(S.items[S.cur].uid, { quiet: true });
  if (!done || document.activeElement === document.body) refocus(opener);
}
function refocus(opener) {
  if (!opener || opener === document.body) return;
  const chip = opener.dataset?.uid && !opener.closest("#list") ? `#detail .rgc[data-uid="${CSS.escape(opener.dataset.uid)}"]` : null;
  queueMicrotask(() => {
    for (const el of [opener, chip && $(chip), $("#list")]) {
      if (el?.isConnected && el.offsetParent !== null) still(el);
      if (document.activeElement === el) return;
    }
  });
}

function metaBits(job) {
  const lab = job.labels || {};
  const also = job.also && job.also.length ? h("span", { title: job.also.map((a) => a.location).join("\n") },
    `+${job.also.length} more location${job.also.length === 1 ? "" : "s"}`) : null;
  return [lab.early ? h("span", "flag", "Be an early applicant") : null,
    job.posted_ts ? h("span", { title: new Date(job.posted_ts).toLocaleString() }, job.posted_found
      ? postedText(job.posted_ts, job.posted_exact, job.posted_found, job.posted_day) : `${job.posted_label} ${exactAge(job.posted_ts, job.posted_exact)}`) : null,
    ...[lab.salary, lab.workplace && lab.workplace[0].toUpperCase() + lab.workplace.slice(1), lab.seniority, lab.employment]
      .filter(Boolean).map((x) => h("span", {}, x)),
    lab.too_senior ? h("span", "warn", "Too senior") : null,
    lab.applied_before ? h("span", "warn", lab.applied_before === "Applied before" || isApplied(job) ? lab.applied_before : `${lab.applied_before} (applied before)`) : null,
    ...(job.flags || []).filter((f) => !f.startsWith("Tailored")).map((f) => h("span", "muted", f)), also];
}

const isApplied = (job) => !!job.app && !["saved", "tailored"].includes(job.app.status);

function actions(job, d) {
  const app = isApplied(job);
  const archived = S.tab === "archived";
  const more = lbl("More", "More actions", "more", (e) => moreMenu(job, d, e.currentTarget), { "aria-haspopup": "menu", "aria-expanded": "false" });
  return h("div", { class: "acts", role: "toolbar", "aria-label": "Job actions" },
    btn(app ? "" : "primary", "Apply on the company site (a)", () => apply(job), "Apply", sm("out")),
    B({ class: `btn${app ? " done" : ""}`, title: "Mark applied (m)", onclick: () => markApplied(job, S.detail?.resume || "") },
      app ? [sm("check"), "Applied"] : "Mark applied"),
    S.g8?.detailBits(job),
    h("span", "acts-icons",
      lbl("Like", "Like: similar roles rank higher (l)", "heart", () => like(job), { "aria-pressed": String(!!job.liked) }),
      lbl("Save", "Save for later (s)", "save", () => save(job), { "aria-pressed": String(job.triage === "saved") }),
      archived ? lbl("Restore", "Restore to the feed", "undo", () => restore(job)) : lbl("Not interested", "Not interested (x)", "ban", () => notInterested(job)),
      lbl("Contacts", FIND_TIP, "person", findContacts),
      job.outside ? null : lbl("Remove", "Remove: duplicate or not wanted", "x", () => removeJob(job), { class: "lbl-btn rm-lbl", title: REMOVE_TIP }),
      more));
}
// W8: every job action says what it does (an icon and a word), not an icon alone
const lbl = (word, tip, id, onclick, attrs = {}) => B({ class: "lbl-btn", "aria-label": tip, title: tip, onclick, ...attrs }, icon(id), h("span", "lbl-w", word));
function findContacts() {
  const sec = $("#people");
  if (!sec) return;
  if (!sec.querySelector(".finder-body").childElementCount) sec.querySelector(".find-c").click();
  sec.scrollIntoView({ block: "start", behavior: calm.matches ? "auto" : "smooth" });
}

function moreMenu(job, d, anchor) {
  const appId = d?.application?.id || job.app?.id;
  openPop(menu(
    item("Copy JD", COPY_TIP, () => copyJd(job.uid)),
    item("Copy JD + keywords", "Copy the JD with the keywords your resume misses (c)", () => copyJd(job.uid, true, S.detail?.resume || "")),
    item("Open the posting ↗", POST_TIP, () => openExt(job.url)),
    appId ? item("Tracker row: edit, contacts, outreach", "Open this job's tracker row", () => openRowEditor(appId)) : null,
    item("Add a job by link", "Add any job to the tracker by its link (n)", openAddJob)), anchor, "More actions");
}

function peopleBlock(job) {
  const body = h("div", "finder-body");
  const open = async () => {
    S.people = job.uid;
    body.replaceChildren(h("p", "muted small", "Loading…"));
    try { (await import("./finder.js")).jobFinder(job, body, (row) => rowMade(job, row)); }
    catch (err) { body.replaceChildren(); fail(err); }
  };
  if (S.people === job.uid) queueMicrotask(open);
  return h("section", { class: "d-sec people", id: "people", "aria-labelledby": "pp-h" }, h("h3", { id: "pp-h" }, "Find contacts"),
    h("p", "muted small", "Free signals first: the JD, the company's team page, LinkedIn's own search pages. Apollo and Hunter " +
      "spend your credits only when you click, with the cost shown first."),
    B({ class: "btn find-c", title: FIND_TIP, onclick: open }, "Find contacts"), body);
}

function rowMade(job, row) {
  if (row?.id && !job.app) job.app = { id: row.id, status: row.status };
  reloadDetail(job.uid);
}

export async function reloadDetail(uid) {
  try { cache.set(uid, await get("/api/job", { uid })); } catch (err) { fail(err); }
  refreshDetail(uid);
}

function worthLine(job) {
  if (job.worth == null) return h("p", "worth-line muted", "Priority: still scoring");
  return h("div", "worth-line", h("p", {}, "Priority ", h("b", {}, job.worth), muted("/100"),
    B({ class: "info", "aria-label": "What Priority means", title: WORTH_TIP, onclick: () => toast(WORTH_TIP) }, "?")),
  isApplied(job) ? h("p", "band", "Already applied") : h("p", `band${job.worth >= 70 ? " hi" : ""}`, job.band));
}

function matchBlock(job, d) {
  const parts = (job.parts || []).map((p, i) => h("li", {}, i < 4 ? quadGlyph(i, p.pct) : h("span", "qg"), h("span", "p-l", p.label),
    h("span", "p-n", p.pct === null ? "n/a" : `${p.pct}%`), h("span", "p-why", p.why)));
  return h("section", { class: "match", id: "breakdown", "aria-labelledby": "m-h" },
    h("div", "ring-col", bigRing(job), job.tailored ? h("p", "small", tailoredLine(job)) : null, worthLine(job)),
    h("div", "parts", h("h2", { id: "m-h" }, "Why this match"), h("ul", "plist", parts), resumeBlock(job, d)));
}

const shortName = (label) => label.replace(" Engineer", " Eng.").replace("Data Scientist", "Data Sci.");
function resumeBlock(job, d) {
  const r = d?.card?.resumes || job.resumes;
  if (!r) return null;
  const how = h("a", { href: r.how, target: "_blank", rel: "noopener", class: "lnk", title: "How the resume estimate is computed (new tab)" },
    "How this estimate is computed", sm("out"));
  if (r.empty || !r.best) return h("div", "resumes", h("p", "muted", r.short), how);
  if (S.detail && !S.detail.resume) S.detail.resume = r.best.id;
  const PICK_TIP = "Resume for Copy JD + keywords and Mark applied";
  const pick = h("select", { "aria-label": PICK_TIP, title: PICK_TIP, onchange: (e) => { S.detail.resume = e.target.value; } },
    r.all.map((x) => h("option", { value: x.id }, `${x.label} (${x.pct}%)`)));
  pick.value = S.detail?.resume || r.best.id;
  const full = d?.card?.resumes?.all?.[0]?.matched ? r.all.map((x) => h("details", "resume-row",
    h("summary", { title: `${x.label} resume: matched and missing keywords` }, `${x.label}: ${x.pct}% (${[x.grade ? `grade ${x.grade}` : null, `generic ${x.generic}%`].filter(Boolean).join(", ")})` +
      ` · ${x.n_matched} matched · ${x.n_missing} missing${x.id === r.best.id ? " (best)" : ""}`),
    h("p", "small", `Matched: ${x.matched.join(", ") || "none"}`),
    h("p", "small", `Missing (most important first): ${x.missing.join(", ") || "none"}`),
    h("p", "small muted", `Parsing checks on this resume file: ${[...(x.parse_fired || []), ...(x.tips || [])].join(", ") || "none fired"}`))) : [];
  return h("div", { class: "resumes", title: r.label },
    h("p", {}, "Best resume ", h("b", {}, r.best.label), muted(` · ${r.short}`)),
    h("ul", "res-bars", r.all.map((x) => {
      const fill = h("i");
      fill.style.transform = `scaleX(${x.pct / 100})`;
      return h("li", { class: x.id === r.best.id ? "best" : null, title: `${x.label}: ${x.pct}% (generic ${x.generic}%)` },
        h("span", {}, shortName(x.label)), h("span", "bar", fill), h("b", {}, `${x.pct}%`));
    })),
    h("p", "small muted", r.label), r.title ? h("p", "small muted", `Title says ${r.title.label}; ${r.best.label} resume scores higher.`) : null,
    h("label", { class: "use-res", title: PICK_TIP }, muted("Use resume "), pick), full.length ? h("div", "resume-rows", full) : null, how);
}

function fitGaps(job) {
  const col = (title, items, cls) => (items && items.length ? h("div", `fg ${cls}`, h("h3", {}, title), h("ul", {}, items.map((t) => h("li", {}, t)))) : null);
  const box = h("section", "fitgaps", col("Why you fit", job.fit, "fit"), col("Gaps", job.gaps, "gap"));
  return box.children.length ? box : null;
}

function atsBox(uid) {
  const body = h("div", {}, h("p", "muted small", "Loading…"));
  const box = h("details", "ats-box", h("summary", { title: "How this ATS reads your resume (loads on open)" }, "How this ATS handles your resume"), body);
  box.addEventListener("toggle", async () => {
    if (!box.open || box.dataset.loaded) return;
    try {
      const d = await get("/api/jd_text", { uid, keywords: "1", resume: S.detail?.resume || "" });
      const a = d.ats || { box: [], keywords: [], questions: [] };
      box.dataset.loaded = "1";
      box.querySelector("summary").textContent = (a.box[0] || "How this ATS handles your resume").replace(/ \(vendor.*$/, "");
      body.replaceChildren(...[...a.box.slice(1), ...a.keywords, ...a.questions].map((line) => (line.startsWith("Sources: ")
        ? h("ol", "small", (line.match(/https:\/\/\S+/g) || []).map((u) => h("li", {}, extLink(u, u))))
        : h("p", "small", line))));
    } catch (err) { body.replaceChildren(h("p", {}, `Could not load: ${err.message}`)); }
  });
  return box;
}

function applicationBlock(d) {
  const a = d?.application;
  if (!a) return null;
  const pairs = [["Status", a.status], ["Applied on", a.applied_on], ["Resume", a.resume], ["Contact", a.target_name], ["Role", a.target_role],
    ["Email", a.email_address], ["Email sent", a.email_sent], ["LinkedIn", a.linkedin_request],
    ["Follow-up 1", a.follow_up_1], ["Follow-up 2", a.follow_up_2]].filter(([, v]) => v);
  const sheet = a.sheet_url || S.sheetUrl;
  return h("section", { class: "side-block", "aria-labelledby": "app-h" }, h("h3", { id: "app-h" }, "Your application"),
    h("dl", "kv", pairs.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])),
    h("div", "c-acts", btn("", "Open this job's tracker row", () => openRowEditor(a.id), "Edit row · contacts · outreach"),
      ...resumeBtns(a.id, !!a.resume, () => refreshDetail(d.uid)),
      a.drive_url ? extLink(a.drive_url, "Open in Drive ↗", "btn ghost", "Open the resume file in Google Drive (new tab)") : null,
      sheet && a.status !== "saved" ? extLink(sheet, "Open in Sheet ↗", "btn ghost", "Open your tracker sheet (new tab)") : null));
}

function companyBlock(d) {
  const c = d?.company_panel;
  if (!c) return null;
  const trend = c.prior_4_weeks === null ? " (a trend needs 5 weeks of board history)" : ` (${c.prior_4_weeks} in the 4 weeks before)`;
  return h("section", { class: "side-block", "aria-labelledby": "co-h" }, h("h3", { id: "co-h" }, `At ${d.company}`),
    h("p", "stat", h("b", {}, c.open_roles), " open matching roles · ", h("b", {}, c.new_this_week), ` new this week${trend}`),
    c.roles.length ? h("ul", "roles", c.roles.map((r) => h("li", {}, B({ class: "r-t lnk-plain", title: "Open this job", onclick: () => openJob(r.uid) }, r.title),
      r.location ? locSpan(r.location, "muted small") : null))) : null);
}

function earlierBlock(d) {
  const e = d?.earlier || [];
  if (!e.length) return null;
  return h("section", "side-block", h("h3", {}, `Earlier applications at ${d.company}`), h("ul", "roles", e.map((x) => h("li", {},
    h("span", {}, `${x.role_title || "(role)"} · ${x.applied_on || "no date"} · ${x.status}`),
    ...Object.entries(x.contacts || {}).map(([k, v]) => h("span", "muted small", `${k.replace(/_/g, " ")}: ${v}`))))));
}

function jdBlock(job, d) {
  if (!d) return h("section", "jd", h("span", "sk-line w90"), h("span", "sk-line w70"), h("span", "sk-line w80"));
  const text = h("div", "jd-text", d.jd || "(no JD stored for this job)");
  const more = B({ class: "lnk", title: "Show the whole JD", onclick: () => { more.textContent = text.classList.toggle("open") ? "Show less" : "Show all"; } }, "Show all");
  return h("section", { class: "jd", "aria-labelledby": "jd-h" }, h("h2", { id: "jd-h" }, "Job description"),
    h("div", "jd-acts", extLink(d.url || job.url, "Original posting ↗", "lnk", POST_TIP),
      B({ class: "lnk", title: COPY_TIP, onclick: () => copyJd(job.uid) }, "Copy JD"), fullJd({ uid: job.uid })),
    text, more);
}

function render(job, d, animate) {
  if (!job) return;
  pane().replaceChildren(h("div", `d-in${animate && !calm.matches ? " enter" : ""}`, grab(),
    h("header", "d-head", h("p", "d-co", h("b", {}, job.company), sponsorBadge(d?.card?.sponsor || job.sponsor), job.location ? locSpan(job.location) : null,
      job.ghost ? h("span", "tag warn", icon("ghost", "ic sm"), job.ghost) : null),
      h("h2", "d-title", job.title), h("p", "d-meta", metaBits(job)), S.g8?.ask(job)),
    actions(job, d), matchBlock(job, d), fitGaps(job), peopleBlock(job), atsBox(job.uid),
    h("div", "side", S.g8?.answers(job), applicationBlock(d), sponsorBlock(d?.card?.sponsor || job.sponsor, job.company), companyBlock(d) || (d ? null : h("section", "side-block", h("span", "sk-line w70"), h("span", "sk-line w50"))), earlierBlock(d),
      d ? lazy("offer", job, d) : null, d ? lazy("appq", job, d) : null),
    jdBlock(job, d)));
}

// GOAL29: Offer check, Application questions
const lazy = (m, job, d) => { const box = h("div"); import(`./${m}.js`).then((x) => x.mount(box, job, d)).catch(fail); return box; };

function grab() {
  const label = "Close (Esc), or swipe down";
  return B({ class: "grab", "aria-label": label, title: label, onclick: () => (S.detail?.panel ? closePanel() : closeSheet()) }, h("span", { "aria-hidden": "true" }));
}
function openSheet() {
  if (!phone.matches) return;
  document.body.classList.add("sheet-open");
  pane().scrollTop = 0;
  still(pane());
}
export function closeSheet() {
  if (!document.body.classList.contains("sheet-open")) return false;
  document.body.classList.remove("sheet-open");
  pane().style.transform = "";
  closePop();
  still($("#list"));
  return true;
}
// swipe down: touch events (iOS scroll cancels pointer ones)
export function wireSwipe() {
  let y0 = null;
  pane().addEventListener("touchstart", (e) => {
    y0 = document.body.classList.contains("sheet-open") && pane().scrollTop <= 0 ? e.touches[0].clientY : null;
  }, { passive: true });
  pane().addEventListener("touchmove", (e) => {
    if (y0 === null) return;
    const dy = e.touches[0].clientY - y0;
    if (dy <= 0) { pane().style.transform = ""; return; }
    e.preventDefault();
    pane().style.transform = `translateY(${dy}px)`;
  }, { passive: false });
  pane().addEventListener("touchend", (e) => {
    if (y0 === null) return;
    const dy = e.changedTouches[0].clientY - y0;
    y0 = null;
    pane().style.transform = "";
    if (dy > SWIPE_CLOSE_PX) (S.detail?.panel ? closePanel() : closeSheet());
  });
}

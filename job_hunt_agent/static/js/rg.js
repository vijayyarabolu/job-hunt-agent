// Lazy: resumegen chips, Generate, panel, outside jobs.
import { OPEN_TIP, $, $$, B, btn, S, api, exactAge, extLink, fail, get, h, muted, openExt, phone, q, store, toast, still } from "./core.js";
import { closePanel, refreshDetail, showPanel } from "./detail.js";
import { RG_ON_KEY, apply, loadFeed, rescore } from "./feed.js";
import { openRowEditor } from "./editor.js";
import { check, confirmOutside, followupsTop, initBack } from "./back.js";
import * as rq from "./rq.js";

const FAST_MS = 4000;
const MAX_FAST_MS = 30000;   // GOAL30-app §0: a resume being made with nothing new: 4, 8, 16, 30 s between asks
const SLOW_MS = 60000;
const MAX_PICK = 20;
const LABEL = { queued: "Resume queued", generating: "Generating resume", ready: "Resume ready", "needs you": "Resume needs you" };
const PICK_TIP = "Select for Generate for selected (Space; Shift-click a range)";
let timer = 0;
let gen = 0;
let wait = FAST_MS;
let seen = "";
let panelId = null;
let lastPick = null;
const bar = h("div", { class: "pickbar", role: "region", "aria-label": "Selected jobs", hidden: true });

S.rg = { on: false, jobs: {}, apps: {}, picks: new Set(), message: "" };

// a Generate on its way (a placeholder, no id yet) shows over the resume the job or its row already has (GOAL33)
const chipOf = (el) => { const sent = el.dataset.uid && S.rg.jobs[el.dataset.uid];
  return sent && sent.id == null ? sent : el.dataset.app ? S.rg.apps[el.dataset.app] : S.rg.jobs[el.dataset.uid]; };
const label = (c) => (c.approved ? "Resume approved" : LABEL[c.chip]);
const when = (ts) => `${new Date(ts).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} · ${exactAge(ts)}`;
const flat = (map) => Object.values(map || {});

export async function poll() {
  clearTimeout(timer);
  const mine = ++gen;
  const pid = S.profile;
  if (document.hidden) return;
  const again = (ms) => { if (mine === gen) { clearTimeout(timer); timer = setTimeout(poll, ms); } };
  try {
    const d = await get("/api/resumes");
    if (mine !== gen || pid !== S.profile) return;
    const was = [...flat(S.rg.jobs), ...flat(S.rg.apps)];
    Object.assign(S.rg, { on: d.online, message: d.message || "", jobs: d.jobs, apps: d.apps });
    document.body.classList.toggle("rg-on", d.online);
    store.set(RG_ON_KEY, d.online ? "1" : "0");
    announce(was, [...flat(d.jobs), ...flat(d.apps)]);
    rescore(d.scores);
    paint();
    rq.counts(d);
    if (d.outside_new && S.tab === "saved") loadFeed({ quiet: true });
    const now = JSON.stringify([d.online, d.jobs, d.apps, d.counts]);
    wait = now === seen ? Math.min(wait * 2, MAX_FAST_MS) : FAST_MS;
    seen = now;
    again(d.active ? wait : SLOW_MS);
  } catch { again(SLOW_MS); }
}

function announce(was, now) {
  const before = new Map(was.map((c) => [c.id, c.chip]));
  for (const c of now) {
    if (before.has(c.id) && before.get(c.id) !== c.chip) {
      if (c.id === panelId) openResume(c.id, { keep: true });
      if (c.chip === "ready" || c.chip === "needs you") toast(`${LABEL[c.chip]}${nameOf(c)}`);
    }
  }
}
function nameOf(c) {
  const uid = Object.keys(S.rg.jobs).find((u) => S.rg.jobs[u].id === c.id);
  const job = S.items.find((j) => j.uid === uid);
  return job ? `: ${job.company} · ${job.title}` : "";
}

export function paint() {
  for (const el of $$(".rgc[data-uid], .rgc[data-app]")) {
    const c = chipOf(el);
    if (!c && el.dataset.uid && el.closest("#list")) { genButton(el); continue; }
    el.removeAttribute("aria-hidden");
    el.className = c ? `rgc rg-${c.chip.replace(" ", "-")}${c.approved ? " rg-ok" : ""}` : "rgc";
    el.textContent = c ? label(c) + (c.badge ? " · Outdated" : "") : "";
    el.title = c ? [`${label(c)}: open it`, c.why, c.made && `made ${when(c.made)}`, c.badge, c.archived && "archived"].filter(Boolean).join(" · ") : "";
    el.onclick = c ? (e) => { e.stopPropagation(); if (c.id != null) openResume(c.id, { opener: e.currentTarget }); } : null;
  }
  for (const b of $$(".rg-gen-label[data-uid]")) b.textContent = S.rg.jobs[b.dataset.uid] ? "Generate again" : "Generate resume";
  paintPicks();
  fitSelBar();
}

function genButton(el) {
  el.className = S.rg.on ? "rgc rg-gen" : "rgc";
  el.textContent = S.rg.on ? "Generate resume" : "";
  el.title = "Generate a tailored resume (g)";
  el.setAttribute("aria-hidden", "true");
  el.onclick = S.rg.on ? (e) => { e.stopPropagation(); generate([el.dataset.uid]); } : null;
}
function fitSelBar() {
  const row = $("#list .row[aria-selected=true]");
  const bar = $("#sel-bar");
  if (!row || bar.hidden) return;
  bar.style.transform = `translateY(${row.offsetTop}px)`;
  bar.style.height = `${row.offsetHeight}px`;
}

export function pick(i, e) {
  const job = S.items[i];
  if (!job || !S.rg.on) return;
  const on = !S.rg.picks.has(job.uid);
  const from = e?.shiftKey && lastPick != null ? Math.min(lastPick, i) : i;
  const to = e?.shiftKey && lastPick != null ? Math.max(lastPick, i) : i;
  for (const j of S.items.slice(from, to + 1)) { if (on) S.rg.picks.add(j.uid); else S.rg.picks.delete(j.uid); }
  lastPick = i;
  paintPicks();
}

function paintPicks() {
  for (const row of $$("#list .row[data-i]")) {
    const job = S.items[Number(row.dataset.i)];
    if (job && S.rg.on) row.setAttribute("aria-checked", String(S.rg.picks.has(job.uid)));
    const box = row.querySelector(".pick");
    if (box && !box.title && !box.dataset.tip) box.title = PICK_TIP;
  }
  if (S.rg.on) $("#list").setAttribute("aria-multiselectable", "true");
  const n = S.rg.picks.size;
  if (!n && bar.contains(document.activeElement)) still($("#list"));
  bar.hidden = !n;
  document.body.classList.toggle("picking", n > 0 || document.body.classList.contains("pick-mode"));
  if (!n) return;
  const over = n > MAX_PICK;
  bar.replaceChildren(h("span", "pb-n", `${n} selected`), ...(over ? [muted(` · pick at most ${MAX_PICK}`, "warn")] : []),
    B({ class: "btn primary", disabled: over, title: "Generate for selected (g)", onclick: () => generate([...S.rg.picks]) },
      "Generate for selected"),
    btn("ghost", "Clear the selection", () => { S.rg.picks.clear(); lastPick = null; paintPicks(); }, "Clear"));
}

function selectToggle() {
  if (!S.rg.on || !["recommended", "saved", "archived"].includes(S.tab)) return null;
  const on = document.body.classList.contains("pick-mode");
  return B({ class: "btn ghost sel-mode", "aria-pressed": String(on), title: on ? "Done selecting" : "Select jobs for Generate for selected",
    onclick: () => { document.body.classList.toggle("pick-mode"); paintPicks(); paintHead(); } }, on ? "Done selecting" : "Select");
}
function paintHead() {
  $(".sel-mode")?.remove();
  const btn = selectToggle();
  if (btn) $("#list-head .lh-top")?.append(btn);
}

const QUEUED = { id: null, chip: "queued", status: "queued", approved: false, why: "Queued for tailoring…" };
const withoutPlaceholders = (jobs, uids) => Object.fromEntries(Object.entries(jobs).filter(([u, c]) => !(uids.includes(u) && c.id == null)));
export async function generate(uids, places = null) {
  if (!S.rg.on) { toast(S.rg.message || "Resume tailoring is not available", { error: true }); return; }
  const picked = new Set(S.rg.picks);
  const had = Object.fromEntries(uids.filter((u) => S.rg.jobs[u]).map((u) => [u, S.rg.jobs[u]]));   // Generate again: put back if not queued
  S.rg.jobs = { ...S.rg.jobs, ...Object.fromEntries(uids.map((u) => [u, QUEUED])) };
  S.rg.picks = new Set();
  lastPick = null;
  paint();
  try {
    const d = await api("/api/resumes/generate", places ? { uids, places } : { uids });
    S.rg.jobs = { ...withoutPlaceholders(S.rg.jobs, uids), ...had, ...d.chips };
    paint();
    const skipped = d.skipped.length ? ` · not queued: ${d.skipped.map((s) => s.why).join("; ")}` : "";
    const at = Object.values(d.positions || {}).filter(Boolean);
    const where = at.length ? ` (#${Math.min(...at)}${at.length > 1 ? ` to #${Math.max(...at)}` : ""})` : "";
    if (d.queued || skipped) toast(d.queued === 1 ? `Added to queue${where}${skipped}` : `Added ${d.queued} to the queue${where}${skipped}`,
      { ms: skipped ? 12000 : 0, extra: d.queued ? B({ class: "undo", title: "Open the Resumes tab's queue", onclick: rq.showQueue }, "View queue") : null });
    if (d.queued) poll();
    if (d.needs_place?.length) askPlaces(d.needs_place);
    return d;
  } catch (err) {
    S.rg.jobs = { ...withoutPlaceholders(S.rg.jobs, uids), ...had };
    S.rg.picks = picked;
    paint();
    if (places) throw err;
    fail(err);
  }
}

// the job's city can't be worked out, or it names several: ask, then queue it (the pick is kept for the job)
let placeDlg = null;
function placeDialog() {
  if (placeDlg) return placeDlg;
  const other = h("input", { type: "text", id: "pd-other", placeholder: "City, ST", autocomplete: "off", hidden: true,
    title: "Type the city as City, ST (Enter saves)", "aria-label": "Other city, as City, ST" });
  placeDlg = h("dialog", { class: "form-dialog place-dlg", "aria-labelledby": "pd-h" },
    h("h2", { id: "pd-h" }, "Which city goes on the resume?"), h("p", { class: "muted", id: "pd-who" }),
    h("div", { class: "row-btns", id: "pd-choices" }), other, h("p", { class: "small", id: "pd-status", role: "status" }),
    h("div", { class: "row-btns" }, B({ class: "btn ghost", id: "pd-cancel", title: "Not now: nothing is queued for this job (Esc)" }, "Cancel")));
  document.body.append(placeDlg);
  return placeDlg;
}
async function askPlaces(asks) {
  for (const [n, ask] of asks.entries()) await askPlace(ask, asks.length > 1 ? ` (${n + 1} of ${asks.length})` : "");
}
function askPlace(ask, count) {
  const dlg = placeDialog();
  const [who, list, other, status] = ["#pd-who", "#pd-choices", "#pd-other", "#pd-status"].map((s) => $(s, dlg));
  who.textContent = `${ask.company} · ${ask.title}${count}${ask.location ? ` · listed as "${ask.location}"` : ""}`;
  status.textContent = "";
  other.value = "";
  other.hidden = true;
  return new Promise((done) => {
    const send = async (value) => {
      status.textContent = "Queuing…";
      status.classList.remove("bad");
      try { await generate([ask.uid], { [ask.uid]: value }); dlg.close(); }
      catch (err) { status.textContent = err.message; status.classList.add("bad"); }
    };
    list.replaceChildren(...ask.choices.map((c) => B({ class: "btn", title: `Put ${c} on the resume and queue it`, onclick: () => send(c) }, c)),
      B({ class: "btn", title: `A remote job: your own city, ${ask.home}`, onclick: () => send("remote") }, `Remote → ${ask.home}`),
      B({ class: "btn ghost", title: "Type the city (Enter saves)", onclick: () => { other.hidden = false; other.focus(); } }, "Other…"));
    other.onkeydown = (e) => { if (e.key === "Enter" && other.value.trim()) { e.preventDefault(); send(other.value.trim()); } };
    $("#pd-cancel", dlg).onclick = () => dlg.close();
    dlg.addEventListener("close", done, { once: true });
    dlg.showModal();
    list.querySelector("button")?.focus();
  });
}
const generateKey = (job) => generate(S.rg.picks.size ? [...S.rg.picks] : [job.uid]);

function detailBits(job) {
  if (!S.rg.on) return muted(S.rg.message || "Resume tailoring is not available", "muted small rg-off");
  const c = S.rg.jobs[job.uid];
  queueMicrotask(paint);
  return [B({ class: "btn rg-gen-label", "data-uid": job.uid, title: "Generate a tailored resume (g)", onclick: () => generate([job.uid]) },
    c ? "Generate again" : "Generate resume"),
  h("button", { type: "button", class: "rgc", "data-uid": job.uid })];
}

export async function openResume(id, { keep = false, opener = null } = {}) {
  if (keep && S.detail?.rg !== id) { panelId = null; return; }
  panelId = id;
  if (!keep) { showPanel([h("h2", {}, "Tailored resume"), muted("Loading…", "muted small")], { opener, close: CLOSE }); S.detail.rg = id; }
  try {
    const d = await get("/api/resumes/job", { id });
    if (S.detail?.rg === id) showPanel(panel(d), { keep: true, close: CLOSE });
  } catch (err) {
    fail(err);
    if (S.detail?.rg === id) { panelId = null; closePanel(); }
  }
}

// v = when the files were made: a note or a re-import writes new files at the same address, so the URL changes
const file = (d, which) => `/api/resumes/file${which === "pdf" && d.file_name ? "/" + encodeURIComponent(d.file_name) : ""}?${q({ id: d.id, which, ...(d.files_version ? { v: d.files_version } : {}) })}`;
const CLOSE = "Close the resume panel (Esc)";
function panel(d) {
  const done = () => { const b = busy.get(d.id); if (b?.sent && !["queued", "generating"].includes(d.chip)) { busy.delete(d.id); paintBusy(); } };
  const show = !phone.matches && d.files.includes("pdf")
    ? keepFocus(h("iframe", { class: "rg-pdf", src: `${file(d, "pdf")}#toolbar=0&navpanes=0&view=FitH`, title: `Tailored resume PDF: ${d.company}`, onload: done }))
    : d.files.includes("png") ? h("img", { class: "rg-png", src: file(d, "png"), alt: `Tailored resume, page 1: ${d.company}`, onload: done, onerror: done }) : null;
  queueMicrotask(show ? paintBusy : done);
  const changed = d.what_changed ? h("p", { class: "rg-changed small", title: "What your last note or Google Doc edit changed" }, d.what_changed) : null;
  return [h("h2", {}, "Tailored resume"), h("p", {}, h("b", {}, d.company), ` · ${d.role}`), status(d), raw(d.ats_html), acts(d), show, changed, raw(d.diff_html)];
}
// Chrome's PDF viewer takes the focus: take it back
const PDF_FOCUS_MS = 4000;
const PDF_WATCH_MS = 100;
function keepFocus(pdf) {
  const back = () => { if (document.activeElement === pdf) still($("#detail")); };
  const onBlur = () => setTimeout(back);
  addEventListener("blur", onBlur);
  const watch = setInterval(back, PDF_WATCH_MS);
  setTimeout(() => { clearInterval(watch); removeEventListener("blur", onBlur); }, PDF_FOCUS_MS);
  return pdf;
}

function status(d) {
  const bits = [LABEL[d.chip] || d.status, d.attempts ? `attempt ${d.attempts}` : "", d.elapsed_s ? `${Math.round(d.elapsed_s)} s` : "",
    d.base ? `from your ${d.base.label} resume` : ""].filter(Boolean).join(" · ");
  const t = d.times || {};
  const why = d.reasons?.length ? d.reasons.map((r) => `${r.area}: ${r.text}`) : d.failures.map((f) => f.message);
  return h("div", "rg-status", h("p", { title: "Status in the Resume app, attempts, time taken, the base resume used" }, bits),
    t.made ? muted(`Made ${when(t.made)}${t.updated ? ` · updated ${when(t.updated)}` : ""}`, "muted small") : null,
    d.archived_at ? h("p", "small", `Archived ${when(d.archived_at)} `, btn("ghost", "Restore it in the Resume app (files and Drive link unchanged)", (e) => act(d, "restore", null, e.currentTarget), "Restore")) : null,
    d.badge ? h("div", "rg-old", h("p", {}, h("b", {}, d.badge), d.outdated?.length ? ` (${d.outdated.join("; ")})` : ""),
      btn("", "Make it again from your current resume (uses Claude)", (e) => act(d, "regenerate", null, e.currentTarget, true), "Make it again from my current resume")) : null,
    d.error && !d.reasons?.length ? h("p", "warn small", d.error) : null,
    why.length ? h("ul", "rg-fails small", why.map((x) => h("li", {}, x))) : null,
    ["queued", "generating"].includes(d.chip) ? muted("This updates by itself while the page is open.", "muted small") : null);
}

// Undo my edit and Cancel ask twice, like the Resume app's own buttons
const ARM_MS = 4000;
function twice(cls, title, label, yes, run) {
  const b = btn(cls, title, (e) => {
    if (b.dataset.armed) { run(e); return; }
    b.dataset.armed = "1";
    b.textContent = yes;
    setTimeout(() => { if (b.isConnected && !b.disabled) { delete b.dataset.armed; b.textContent = label; } }, ARM_MS);
  }, label);
  return b;
}

function acts(d) {
  const ready = d.chip === "ready", review = ready || d.chip === "needs you";
  const note = h("input", { type: "text", class: "rg-note", "aria-label": "Note: what to fix, or for the next try", title: "Note: what to fix, or for the next try",
    placeholder: "Note, e.g. only make the summary shorter" });
  const doing = (name, extra) => (e) => act(d, name, extra?.(), e.currentTarget);
  const again = d.approved && d.approved_stale;
  return h("div", "rg-acts",
    h("div", "row-btns",
      ready && (!d.approved || again) ? t(btn("primary", again ? "Put this edited version in Drive: the same file and link, so your Sheet's Resume chip shows it"
        : "Approve this resume: upload it to Drive", doing("approve"), again ? "Approve again" : "Approve"), "approve") : null,
      d.approved ? extLink(d.approved.link, "Approved ✓ Open in Drive ↗", "btn done", again ? "Drive has the version before your edit" : "Open in Drive") : null,
      ready ? h("a", { class: "btn", href: file(d, "pdf") + "&dl=1", download: "", title: "Download the PDF" }, "Download PDF") : null,
      d.gdoc_link ? extLink(d.gdoc_link, "Google Doc ↗", "btn", "Open the Google Doc")
        : review ? t(btn("", "Open as a Google Doc: edit it yourself, then press Get my Doc edits", doing("gdoc"), "Open as Google Doc"), "gdoc") : null,
      d.gdoc_link ? t(btn("", "Bring your edits from the Google Doc into this resume", doing("reimport"), "Get my Doc edits"), "reimport") : null),
    review ? h("div", "row-btns",
      d.chip === "needs you" ? t(btn("primary", "Accept it as it is: it becomes ready", doing("accept"), "Accept as it is"), "accept") : null,
      t(btn("", "One Claude call that changes only what your note names; everything else stays as it is", (e) => {
        if (!note.value.trim()) { toast("Write what to fix first, e.g. only make the summary shorter", { error: true }); note.focus(); return; }
        doing("fix", () => note.value.trim())(e); }, "Fix with note"), "fix"),
      d.undo_count ? t(twice("", "Back to the version before your last note or Doc edits (press twice)", "Undo my edit", "Yes, undo it", doing("undo_edit")), "undo_edit") : null,
      d.undo_count != null ? t(twice("ghost", "Drop this resume: it goes to Archived (Restore brings it back) and is never uploaded (press twice)",
        "Cancel", "Yes, cancel it", doing("cancel")), "cancel") : null) : null,
    review ? h("div", "row-btns", note,
      t(btn("", "Make the whole resume again from the base resume, with the note if you wrote one (uses Claude). To change one thing, use Fix with note", doing("regenerate", () => note.value.trim()), "Start over"), "regenerate")) : null);
}

const DOING = { approve: "Uploading to Drive…", gdoc: "Making a Google Doc…", reimport: "Getting your Doc edits…", regenerate: "Queued again",
  accept: "Accepting…", fix: "Fixing with your note…", restore: "Restoring…", undo_edit: "Undoing your edit…", cancel: "Cancelling…" };
const REQUEUE = ["regenerate", "fix"];
const SHOWS = ["reimport", "undo_edit", ...REQUEUE];   // these end once the new resume is on screen
const UNDO_MS = 8000;
// (user 22:23): a pressed action stays busy, with its time, until its result is on screen; the resume's other
// actions wait; a redraw keeps it (resumegen's own page does the same since its GOAL5)
const busy = new Map();
const BUSY_MAX_MS = { short: 60000, long: 15 * 60000 };
const t = (b, name) => { if (b) b.dataset.act = name; return b; };
let ticker = 0;
function paintBusy() {
  for (const [id, e] of busy) if (Date.now() - e.since > BUSY_MAX_MS[e.long ? "long" : "short"]) busy.delete(id);
  if (busy.size && !ticker) ticker = setInterval(paintBusy, 1000);
  if (!busy.size) { clearInterval(ticker); ticker = 0; }
  const e = busy.get(panelId), acts = $("#detail .rg-acts");
  if (!acts) return;
  const text = e && `${e.name === "regenerate" ? "Regenerating…" : DOING[e.name]}${e.step ? ` ${e.step} ·` : ""} ${Math.round((Date.now() - e.since) / 1000)} s`;
  for (const x of acts.querySelectorAll("button[data-act]")) {
    if (e) {
      x.dataset.idle ??= x.textContent;
      x.disabled = true;
      if (e.name === x.dataset.act) x.setAttribute("aria-busy", "true"); else x.removeAttribute("aria-busy");
      x.textContent = e.name === x.dataset.act ? text : x.dataset.idle;
    } else if (x.dataset.idle != null) {
      x.textContent = x.dataset.idle;
      delete x.dataset.idle;
      delete x.dataset.armed;
      x.disabled = false;
      x.removeAttribute("aria-busy");
    }
  }
  let line = $("#detail .rg-busy");
  if (!e) { line?.remove(); return; }
  if (!line) acts.before(line = h("p", { class: "rg-changed small rg-busy", role: "status" }));
  line.textContent = text;
}
async function act(d, name, note, _btn, current = false) {
  const uid = d.source_job_id;
  const was = uid ? S.rg.jobs[uid] : null;
  if (busy.has(d.id)) return;
  const e = { name, since: Date.now(), long: REQUEUE.includes(name), sent: false };
  busy.set(d.id, e);
  paintBusy();
  const steps = name === "reimport" ? setInterval(() => get("/api/resumes/job", { id: d.id })        // resumegen's step
    .then((v) => { e.step = v.step; paintBusy(); }, () => {}), 1000) : 0;
  if (REQUEUE.includes(name) && was) {
    S.rg.jobs = { ...S.rg.jobs, [uid]: { ...was, chip: "queued", status: "queued" } };
    paint();
    showPanel(panel({ ...d, chip: "queued", status: "queued", error: null }), { keep: true, close: CLOSE });
  }
  toast(DOING[name]);
  try {
    const r = await api("/api/resumes/action", { id: d.id, action: name, note: note || null, current }).finally(() => clearInterval(steps));
    e.sent = true;
    e.step = null;
    if (!SHOWS.includes(name)) busy.delete(d.id);
    if (name === "approve") toast(r.updated ? `Approved again: ${r.drive?.name || "the Drive file"} now has this version (same link, so your Sheet's chip shows it)`
      : `Approved: ${r.drive?.name || "saved to Drive"}${r.row_note ? ` · ${r.row_note}` : ""}`);
    if (name === "undo_edit") toast(`Back to the version before your ${r.kind === "fix" ? "note" : "re-import"}${r.left ? ` (${r.left} more to undo)` : ""}`);
    if (name === "cancel") toast("Cancelled: moved to Archived", { ms: UNDO_MS, extra: B({ class: "undo", title: "Restore it", onclick: () => act(r.panel || d, "restore") }, "Undo") });
    if (name === "gdoc" && r.link) toast("Google Doc ready: use the Google Doc ↗ button");
    if (S.detail?.rg === d.id) showPanel(panel(r.panel), { keep: true, close: CLOSE });
    poll();
  } catch (err) {
    busy.delete(d.id);
    fail(err);
    if (was && REQUEUE.includes(name)) { S.rg.jobs = { ...S.rg.jobs, [uid]: was }; paint(); }
    if (S.detail?.rg === d.id) showPanel(panel(d), { keep: true, close: CLOSE });
  }
  paintBusy();
}

function raw(html) {
  const part = document.createRange().createContextualFragment(html || "");
  if (phone.matches) part.querySelector("details")?.removeAttribute("open");
  return part;
}

function outside(meta) {
  const rows = meta.outside || [];
  if (S.tab !== "saved" || !rows.length) return [];
  return [h("div", "section-head", h("h2", {}, `Outside jobs (${rows.length}) `, muted("from JDs pasted in the Resume app"))),
    h("ul", "apps", rows.map((a) => h("li", "out-row",
      B({ class: "row app-row", title: OPEN_TIP, onclick: () => openRowEditor(a.id) }, h("span", "ring-blank"),
        h("span", "row-main", h("span", "row-title", h("span", {}, a.role_title || "(role)")),
          h("span", "row-meta", h("span", "co", a.company), h("span", { class: "tag soft", title: "A JD pasted in the Resume app, not from a job board" }, "outside job"),
            h("span", { class: "rgc", "data-app": a.id })))),
      h("span", "row-btns out-acts",
        a.job_url ? btn("ghost", "Open the posting to apply (I will ask later whether you did)", () => { openExt(a.job_url); api("/api/apply_click", { app: a.id }).catch(fail); }, "Apply ↗") : null,
        btn("", "Mark applied and fill your sheet row", () => confirmOutside(a), "Mark applied")))))];
}

function profile() {
  S.askIn.clear();
  S.ans = null;
  rq.loadAnswers();
  S.rg.picks.clear();
  S.rg.jobs = {};
  S.rg.apps = {};
  poll();
  check(false);
}

export function init8() {
  const css = h("link", { rel: "stylesheet", href: "/rg.css" });
  document.head.append(css);
  document.body.append(bar);
  S.g8 = { paint, pick, generateKey, detailBits, outside, profile, settings: () => S.openSettings(), followupsTop, paintHead,
    poll, apply, openResume, confirmOutside, row: rq.row, head: rq.head, rqEmpty: rq.empty, inline: rq.inline, inHand: rq.inHand, ask: rq.ask, answers: rq.answers,
    loadAnswers: rq.loadAnswers };
  rq.loadAnswers();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  initBack();
  poll().then(() => {
    paintHead();
    if (S.detail?.uid) refreshDetail(S.detail.uid);
    if (S.tab === "saved" || S.tab === "resumes") loadFeed({ quiet: true });
  });
}

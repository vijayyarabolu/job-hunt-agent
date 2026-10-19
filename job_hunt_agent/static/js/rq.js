// , lazy: Resumes tab, "Applied?", resume in hand, answers.
import { $, $$, B, JOB_TABS, S, api, copyText, exactAge, fail, get, h, mini, muted, q, toast } from "./core.js";
import { resumeBtns } from "./dialogs.js";
import { loadFeed, markApplied, notInterested, prog } from "./feed.js";
import { refreshDetail } from "./detail.js";
import { applied } from "./back.js";

let sig = "";
let shown = null;
S.askIn = new Map();
const reload = () => { loadFeed({ quiet: true }); S.g8.poll(); };
export const showQueue = () => { S.rq = "queue"; if (S.tab === "resumes") loadFeed(); else S.switchTab("resumes"); };

export function counts(d) {
  const c = d.counts || {};
  for (const el of $$('[data-tab="resumes"] .n')) el.textContent = c.ready || "";
  const tab = $('#tabs [data-tab="resumes"]');
  if (tab) tab.title = `Tailored resumes (4): ${c.ready || 0} ready${c.needs ? ` (${c.needs} need you)` : ""} · ${c.queue || 0} in queue`;
  if (d.applied_today != null) {
    S.prog = d;
    for (const el of $$(".prog")) el.replaceWith(prog(el.textContent[0] === " " ? " · " : "") || "");
  }
  const now = JSON.stringify([c, Object.values(d.jobs || {}).map((x) => x.status)]);
  if (S.tab === "resumes" && sig && now !== sig) loadFeed({ quiet: true });
  sig = now;
}

// W13: "Queue" means the same in both apps: being made or waiting its turn
const SEG_TIPS = { queue: "Being made or waiting its turn", ready: "Ready to apply with; the ones that need you come first",
  done: "Applied today with a tailored resume" };

export function head(m) {
  const c = m.counts || {};
  return [h("div", "lh-top", h("h1", { id: "lh-title" }, "Tailored resumes ", muted(m.online === false ? m.message : ""))),
    h("p", "small", prog("", m.goal != null ? m : S.prog)),
    h("div", { class: "seg rq-seg", role: "group", "aria-label": "Show" }, [["queue", "Queue"], ["ready", "Ready"], ["done", "Done today"]]
      .map(([v, label]) => B({ "aria-pressed": String(S.rq === v), title: SEG_TIPS[v],
        onclick: () => { S.rq = v; S.cur = 0; loadFeed(); } }, `${label} ${c[v] || 0}`,
        v === "ready" && c.needs ? " " : null, v === "ready" && c.needs ? h("span", { class: "tag warn rq-needs" }, `${c.needs} need${c.needs === 1 ? "s" : ""} you`) : null))),
    m.cancelled_closed ? muted(`Cancelled ${m.cancelled_closed} queued: posting closed`, "muted small") : null];
}

const act = (x, action) => api("/api/resumes/action", { id: x.rg, action }).then(reload, fail);
function bits(job, x) {
  if (x.state === "done") {
    return [h("span", "tag ok", "Applied today"),
      mini("Undo", "Undo: back to Ready", () => api("/api/tracker/undo", { app: x.app, ready: 1 }).then(() => { toast("Back in Ready"); reload(); }, fail)),
      ...resumeBtns(x.app, x.cv, reload)];
  }
  const run = x.state === "generating";
  const shut = x.closed && h("span", { class: "tag warn", title: "The company closed this posting" }, "Posting closed");
  if (run || ["queued", "waiting"].includes(x.state)) {
    return [shut, h("span", "tag soft", run ? ["Generating now", x.started_at && [" · ", h("time", { "data-ts": x.started_at, "data-exact": "1" },
      exactAge(x.started_at).replace(" ago", ""))]] : x.state === "waiting" ? "Waiting (Claude limit or sign-in)" : `#${x.pos} in queue`),
    x.pos > 1 ? mini("Move to top", "Generate this one next", () => act(x, "top")) : null,
    mini("Cancel", run ? "Stop it after the current step" : "Take it out of the queue", () => act(x, "cancel"))];
  }
  return [x.state === "needs" ? h("span", { class: "tag warn", title: x.why || "Review it: Accept as it is, or Fix with note" }, "Needs you") : null,
    shut,
    x.ready_at ? muted(`ready ${exactAge(x.ready_at)}`, "muted small") : null,
    mini("Apply ↗", "Apply on the company site (a)", () => S.g8.apply(job)),
    mini("Mark applied", "Mark applied (m)", () => markApplied(job)),
    mini("Review resume", "Open the tailored resume", () => S.g8.openResume(x.rg)),
    job.outside ? null : mini("Not interested", "Not interested (x)", () => notInterested(job)), ...(x.app ? resumeBtns(x.app, x.cv, reload) : [])];
}
export function row(job) {
  const ask = S.askIn.get(job.uid);
  return job.rq || ask ? h("div", "rq-line", job.rq && S.tab === "resumes" ? [job.rq.n && h("b", "rq-n", `${job.rq.n} · ${job.rq.time}`), bits(job, job.rq)] : null, ask && askLine(job, ask)) : null;
}

export function inline(entries) {
  const key = (e) => e.job_uid || `app:${e.app_id}`;
  const mine = JOB_TABS.includes(S.tab) ? entries.filter((e) => S.items.some((j) => j.uid === key(e))) : [];
  const fresh = mine.filter((e) => !S.askIn.has(key(e)));
  for (const e of mine) S.askIn.set(key(e), e);
  if (fresh.length) { loadFeed({ quiet: true }); fresh.forEach((e) => refreshDetail(key(e))); }
  return entries.filter((e) => !mine.includes(e));
}
function askLine(job, e) {
  const answer = (reply) => async () => {
    S.askIn.delete(job.uid);
    refreshDetail(job.uid);
    try {
      const r = await api("/api/pending/answer", { id: e.id, answer: reply === "pass" ? "no" : reply });
      if (reply === "yes") done(job, r); else if (reply === "pass") notInterested(job); else loadFeed({ quiet: true });
    } catch (err) { fail(err); loadFeed({ quiet: true }); }
  };
  return h("span", "ask-in", h("b", {}, "Applied?"),
    mini("Yes", "Yes: mark it applied (its resume goes to Drive and the tracker)", answer("yes")),
    mini("Not yet", "Not yet: it stays here", answer("no")),
    mini("Not interested", "Not interested: it leaves your lists", answer("pass")));
}
function done(job, r) {
  const n = r.ask?.length;
  toast(`Applied: ${job.company}${r.attached?.kind === "tailored" ? " · tailored resume attached" : ""}`, { extra: [
    n ? B({ class: "undo", title: "Fill the sheet columns still empty", onclick: () => applied(r, () => {}) }, `Fill ${n} sheet field${n > 1 ? "s" : ""}`) : null,
    S.tab !== "resumes" ? B({ class: "undo", title: "Go to the next Ready job", onclick: () => { S.rq = "ready"; S.switchTab("resumes"); } }, "Next ready ›") : null] });
  reload(); // keeps the selected job, or the next one
}
export const ask = (job) => (S.askIn.has(job.uid) ? h("div", "rq-line", askLine(job, S.askIn.get(job.uid))) : null);

export function inHand(job) {
  const c = S.rg?.jobs?.[job.uid];
  const id = job.rq ? job.rq.state === "ready" && job.rq.rg : c?.chip === "ready" && c.id;
  if (!S.ans?.apply_in_hand || !id) return;
  h("a", { href: `/api/resumes/file?${q({ id, which: "pdf", dl: 1 })}`, download: "" }).click();
  const facts = [job.company, job.title, job.req_id && `Req ${job.req_id}`].filter(Boolean).join(" · ");
  copyText(facts, `Resume downloading · copied: ${facts}`);
}

const money = (v) => `$${Math.round(v).toLocaleString("en-US")}`;
let ed = null;     // the open answers editor; a redraw of the same job keeps it (and what is typed)
export function answers(job, focus) {
  if (ed && !focus && shown?.uid === job.uid) return ed;
  shown = job;
  ed = null;
  const a = S.ans?.answers;
  if (!a) return h("section", { class: "ans", hidden: true });
  const [lo, hi] = job.salary || [];
  const posted = lo ? money(lo) + (hi && hi !== lo ? ` to ${money(hi)}` : "") : "";     // W10: its own chip
  const val = a;
  const redo = (k) => $("#detail .ans")?.replaceWith(answers(job, k));
  const sec = h("section", { class: "side-block ans", "aria-labelledby": "ans-h" }, h("h3", { id: "ans-h" }, "Answers on hand ",
    focus ? null : B({ class: "lnk", title: "Edit your answers", onclick: () => redo("work_auth") }, "Edit")),
    focus ? ansForm(focus, () => { ed = null; redo(); }) : h("div", "ans-chips", S.ans.answer_labels.map(([k, label]) => (val[k]
      ? B({ class: "chip", title: `Copy: ${val[k]}`, onclick: () => copyText(val[k], `Copied ${label}: ${val[k]}`) }, label)
      : B({ class: "chip ghost", title: `${label}: not set yet (click to add)`, onclick: () => redo(k) }, `${label} +`))),
    posted ? B({ class: "chip", title: `Copy the job's posted pay: ${posted}`, onclick: () => copyText(posted, `Copied the posted pay: ${posted}`) }, "Posted pay") : null));
  if (focus) ed = sec;
  return sec;
}
function ansForm(focus, done) {
  const ins = S.ans.answer_labels.map(([k, label]) => h("input", { type: "text", name: k, maxlength: "300", autocomplete: "off",
    value: S.ans.answers[k] || "", "aria-label": label }));
  setTimeout(() => (ins.find((x) => x.name === focus) || ins[0]).focus());
  const save = () => api("/api/settings/apply", { answers: Object.fromEntries(ins.map((x) => [x.name, x.value])) })
    .then(async () => { ed = null; await loadAnswers(); toast("Answers saved"); }, fail);
  return h("form", { class: "ans-ed", onsubmit: (e) => { e.preventDefault(); save(); },
    onkeydown: (e) => { if (e.key === "Escape") { e.stopPropagation(); done(); }
      if (e.key === "Enter" && !e.isComposing && e.target.tagName === "INPUT") { e.preventDefault(); save(); } } },
    S.ans.answer_labels.map(([, label], i) => h("label", "field", h("span", {}, label), ins[i])),
    B({ class: "btn sm", type: "submit" }, "Save"), " ", B({ class: "btn sm ghost", onclick: done }, "Cancel"));
}
export async function loadAnswers() {
  const pid = S.profile;
  try {
    const s = await get("/api/settings");
    if (pid !== S.profile) return;
    S.ans = s;
    if (shown) $("#detail .ans")?.replaceWith(answers(shown)); // the chips only: a redraw would drop the focus
  } catch { /* the chips wait */ }
}

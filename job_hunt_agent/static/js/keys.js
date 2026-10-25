// Keys (? sheet); ⌘K palette loads on first use.
import { $, $$, JOB_TABS, S, TABLE_TABS, TABS, closePop, openExt, toast } from "./core.js";
import { apply, copyJd, cur, like, markApplied, notInterested, openCard, removeJob, restore, save, select } from "./feed.js";
import { closePanel, closeSheet, openJob } from "./detail.js";
import { openAddJob } from "./editor.js";
import { undoLastEdit } from "./inline.js";

const TYPING = /^(INPUT|SELECT|TEXTAREA)$/;
let app = {};

const resume = () => S.detail?.resume || "";
const anyDialogOpen = () => $$("dialog[open]").length > 0;

function jobKeys(e) {
  const job = cur();
  const acts = {
    j: () => select(S.cur + 1), ArrowDown: () => select(S.cur + 1),
    k: () => select(S.cur - 1), ArrowUp: () => select(S.cur - 1),
    Enter: () => openCard(job, { sheet: true }), o: () => openExt(job.url),
    a: () => apply(job), m: () => markApplied(job, resume()), s: () => save(job), l: () => like(job),
    x: () => (S.tab === "archived" ? restore(job) : notInterested(job)), X: () => removeJob(job),
    c: () => copyJd(job.uid, true, resume()), C: () => copyJd(job.uid, false),
    g: () => S.g8?.generateKey(job), ...(S.rg?.on ? { " ": () => S.g8.pick(S.cur) } : {}),
    b: async () => { await openJob(job.uid, { sheet: true }); $("#breakdown")?.scrollIntoView({ block: "start" }); },
  };
  if (!job || !acts[e.key]) return false;
  e.preventDefault();
  acts[e.key]();
  return true;
}

function escape() {
  if (closePop()) return;
  if (document.body.classList.contains("panel-open")) { closePanel(); return; }
  closeSheet();
}

function onKey(e) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
  if (e.metaKey || e.ctrlKey || e.altKey || anyDialogOpen()) return;
  if (TYPING.test(e.target.tagName) || e.target.isContentEditable) {
    if (e.key === "Escape") e.target.blur();
    return;
  }
  if (S.tab === "settings") {                                  // the Settings page takes only these keys
    if (e.key === "Escape") { if (!closePop()) app.closeSettings(); } else if (e.key === "?") $("#help").showModal(); else if (e.key === "t") app.toggleTheme();
    return;
  }
  if (e.key === "Escape") { escape(); return; }
  if ((e.key === "Enter" || e.key === " ") && e.target.closest("button, a, summary, [contenteditable], td.editable")) return;
  const global = {
    "?": () => $("#help").showModal(),
    "/": () => (TABLE_TABS.includes(S.tab) ? import("./tracker.js").then((m) => m.focusSearch())     // GOAL35: the page's own box
      : $("#pane-list:not([hidden]) .f-search, #pane-list:not([hidden]) .ap-search, #view:not([hidden]) .af-search")?.focus() ?? openPalette()),
    t: () => app.toggleTheme(), n: () => openAddJob(), u: () => undoLastEdit(), ",": () => app.openSettings(),
  };
  if (global[e.key]) { e.preventDefault(); global[e.key](); return; }
  if (e.key >= "0" && e.key <= "9") { app.switchTab(TABS[(Number(e.key) + 9) % 10]); return; }   // 0 is the 10th: Answers
  if (JOB_TABS.includes(S.tab)) jobKeys(e);
}

const openPalette = () => import("./palette.js").then((m) => m.openPalette(app))
  .catch(() => toast("Search could not load: reload the page", { error: true }));

export function initKeys(given) {
  app = given;
  document.addEventListener("keydown", onKey);
  $("#cmdk").addEventListener("click", openPalette);
}

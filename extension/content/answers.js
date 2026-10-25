// Job Hunt Agent Autofill 1.5 (GOAL29 §5): Claude-written answers to a form's open questions. An open question is a big text
// box ("Why do you want to work here?", "Tell us about a project you are proud of"): a textarea, or a text box that
// takes 200 or more characters, with no saved answer, that is not a salary, date, link or yes / no question. It shows
// empty with Generate: nothing is written until the user clicks, and nothing goes into the page until the user clicks Fill this
// field (never Next or Submit). Job Hunt Agent hands the question to resumegen's queue (the user's own Claude login). Pure (no DOM)
// apart from block(), which the panel draws with its own el / btn: unit-tested in Node.
// GOAL30 §2: a box already holding text (an answer saved for another company, or one the site kept) is still an open
// question: it says whose job the text was written for, flags another company's name in it, and offers Regenerate.
// GOAL36 §5.3 / §5.4: a one-line box that asks why / describe / tell us is an open question too (custom forms leave
// out the limit); a box the user's saved answer filled offers "Generate for this job" (the saved answer stays filled and
// saved; only Fill this field / Save to Answers change them); every way it can end without an answer says why, with
// an icon and Retry: not linked to a job, job-hunt-agent or the Resume app not answering, the worker stopped, the queue
// busy, the usage limit, no login, an empty answer, taking too long.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const MIN_CHARS = 200;                         // a text box that takes this much is a written answer
  const NOT_OPEN = /\b(salary|compensation|desired pay|pay (expectation|range|requirement)s?|hourly rate|dates?|url|link|web ?site|portfolio|linked ?in|github|e-?mail|phone)\b/i;
  const YES_NO = /^(are|do|did|have|has|will|would|can|could|is|were)\s+you\b/i;
  const ASKS_MORE = /\b(why|how|describe|explain|tell us|share|example|what (makes|interests|excites|draws))\b/i;
  // a one-line box with no limit is open when its wording asks for a written answer (GOAL36 §5.3)
  const LONG_ASK = /\b(why|describe|explain|tell us|walk us through|share an example|what (makes|interests|excites|draws))\b/i;
  const NOT_LONG = /\bhow did you (hear|find|learn)\b|\breferr/i;
  const SLOW_AFTER = 120;                        // polls at 1 s (2 minutes), then every 5 s while it waits in the queue
  const WAIT_MS = { writing: 1000, limit: 60000 };   // GOAL30 §5: every second while Claude writes; slowly while paused
  const MAX_POLLS = 336;                         // 2 minutes at 1 s + 18 at 5 s: 20 minutes, then it says so
  const GIVE_UP = "Not written after 20 minutes: the Resume app may be stopped or busy with resumes. Retry asks again";
  // every state is an icon + words (readable with red-green colour blindness): never a colour alone
  const LOOK = { writing: "⟳", ready: "✓", limit: "⏸", login: "⚠", failed: "✕", stalled: "⚠" };

  function bigBox(row) {
    if (!row || !row.label || row.label.length < 8 || row.group) return false;
    if (row.kind === "textarea" || (row.kind === "text" && Number(row.max) >= MIN_CHARS)) return true;
    return row.kind === "text" && !Number(row.max) && row.label.length >= 20 && LONG_ASK.test(row.label) && !NOT_LONG.test(row.label);
  }
  const asksWriting = (row) => !NOT_OPEN.test(row.label) && !(YES_NO.test(row.label) && !ASKS_MORE.test(row.label));

  function isOpen(row) {
    if (!bigBox(row) || row.key) return false;                                 // the classifier knows it (cover letter too)
    return asksWriting(row);
  }
  // GOAL36 §5.4: a box the user's saved answer filled (job-hunt-agent matched it to a saved question): Generate for this job too
  function hasSaved(row) {
    if (!bigBox(row) || !row.value || !row.saved || row.key === "cover_letter") return false;
    return asksWriting(row);
  }
  const canWrite = (row) => isOpen(row) || hasSaved(row);

  // the questions of these rows to look up in the cache (once each per job); a saved answer's too (§5.4: the one
  // written for this job shows again when the user comes back)
  function toLookUp(rows, asked) {
    return [...new Set((rows || []).filter((r) => (isOpen(r) && !r.value) || hasSaved(r)).map((r) => r.label))].filter((q) => !asked.has(q));
  }
  // GOAL30 §2: open questions already holding text, to ask Job Hunt Agent whose job it was written for (once per text)
  function filledToCheck(rows, asked) {
    const out = {};
    for (const r of rows || []) if (canWrite(r) && r.value && !asked.has(`${r.label}\u0000${r.value}`)) out[r.label] = String(r.value).slice(0, 6000);
    return out;
  }

  // 0 = done (ready, failed, no login); a stalled one (no worker) is still asked about, slowly
  function waitFor(view, n = 0) {
    if (!view) return 0;
    if (view.state === "writing") return n < SLOW_AFTER && !view.stalled && !view.queued ? WAIT_MS.writing : 5000;
    return WAIT_MS[view.state] || 0;
  }
  // GOAL36 §5.3: what the panel shows when a reply is missing or empty; never a blank or a spinner that never ends
  function settle(view, question) {
    if (!view || typeof view !== "object" || !view.state) return { state: "failed", label: "No answer", error: "the Resume app sent no answer back", question };
    if (view.state === "ready" && !String(view.text || "").trim()) return { ...view, state: "failed", label: "No answer", error: "Claude wrote an empty answer" };
    return view;
  }
  const lookOf = (view) => LOOK[view.state === "writing" && view.stalled ? "stalled" : view.state] || "·";

  function words(view) {
    if (view.state === "failed") return `No answer: ${String(view.error || "it did not pass the checks").replace(/^No answer that passes the checks: /, "")}`;
    if (view.state === "limit") return `${view.label}: it writes on its own after that`;
    return view.label;
  }
  // which states offer Retry: every one that ended without an answer, the usage limit and a stopped worker
  const retries = (view) => ["failed", "login", "limit"].includes(view.state) || (view.state === "writing" && Boolean(view.stalled));

  // the answer under its row: status, the text, and Copy / Fill this field / Regenerate / Shorter / Save to Answers
  function block(view, h, el, btn) {
    view = settle(view, view && view.question);
    const head = el("div", { class: `ans-st ${view.state}${view.stalled ? " stalled" : ""}`, role: "status" }, el("span", { "aria-hidden": "true" }, lookOf(view)), words(view));
    if (view.state !== "ready") {
      const again = retries(view) ? btn("Retry", (e) => h.generate("", e), "link", "Ask Claude again (a click while it still waits does not ask twice)") : null;
      const link = view.unlinked && h.link ? btn("Link to a job", h.link, "link", "Pick the job this page is for") : null;
      return el("div", { class: "ans" }, head, again, link);
    }
    return el("div", { class: "ans" }, head,
      el("div", { class: "txt" }, view.text), el("div", { class: "small" }, `${view.words} words`),
      el("div", { class: "btns" }, btn("Copy", h.copy, "btn"), btn("Fill this field", h.fill, "btn", "Puts it into the page's box (you still click Next yourself)"),
        btn("Regenerate", (e) => h.generate("regenerate", e), "link", "A different answer from the same facts"),
        btn("Shorter", (e) => h.generate("shorter", e), "link", "The same answer, shorter"),
        btn("Save to Answers", h.save, "link", "Keep it in Job Hunt Agent › Answers for the same question elsewhere")));
  }

  // GOAL30 §2: a filled open question with no answer from Claude yet: whose job the text was written for, another
  // company's name in it (▲, so the user never submits the wrong name), and Regenerate for this job + Copy / Fill / Shorter
  // / Save on the text that is there
  // GOAL36 §5.4: on a saved answer the first button is "Generate for this job" (a new answer from this JD and resume;
  // the saved one stays in the box and in Answers until the user clicks Fill this field or Save to Answers)
  function filledBlock(r, flags, h, el, btn) {
    const written = (flags && flags.label) || (r.origin ? `Written for ${r.origin.company}${r.origin.title ? `, ${r.origin.title}` : ""}` : "")
      || (hasSaved(r) ? "Your saved answer is filled" : "");
    const first = hasSaved(r)
      ? btn("Generate for this job", (e) => h.generate("", e), "btn", "Claude writes one for this job from this JD and your resume; your saved answer stays as it is unless you click Save to Answers")
      : btn("Regenerate for this job", (e) => h.generate("regenerate", e), "btn", "Claude writes it again for this job, from the same facts");
    return el("div", { class: "ans" },
      written ? el("div", { class: "ans-st origin" }, el("span", { "aria-hidden": "true" }, "↺"), written) : null,
      flags && flags.names ? el("div", { class: "ans-st names", role: "status" }, el("span", { "aria-hidden": "true" }, "▲"), `Names ${flags.names}`) : null,
      el("div", { class: "btns" }, first,
        btn("Copy", h.copy, "link"), btn("Fill this field", h.fill, "link", "Puts it into the page's box (you still click Next yourself)"),
        btn("Shorter", (e) => h.generate("shorter", e), "link", "The same answer, shorter"),
        btn("Save to Answers", h.save, "link", "Keep it in Job Hunt Agent › Answers for the same question elsewhere")));
  }

  JH.answers = { isOpen, hasSaved, canWrite, toLookUp, filledToCheck, filledBlock, waitFor, settle, words, retries, lookOf, block,
    LOOK, MAX_POLLS, GIVE_UP, MIN_CHARS };
  if (typeof module !== "undefined") module.exports = JH.answers;
})();

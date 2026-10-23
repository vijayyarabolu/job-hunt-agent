// Job Hunt Agent Autofill 1.5 (GOAL29 §5): Claude-written answers to a form's open questions. An open question is a big text
// box ("Why do you want to work here?", "Tell us about a project you are proud of"): a textarea, or a text box that
// takes 200 or more characters, with no saved answer, that is not a salary, date, link or yes / no question. It shows
// empty with Generate: nothing is written until the user clicks, and nothing goes into the page until the user clicks Fill this
// field (never Next or Submit). Job Hunt Agent hands the question to resumegen's queue (the user's own Claude login). Pure (no DOM)
// apart from block(), which the panel draws with its own el / btn: unit-tested in Node.
// GOAL30 §2: a box already holding text (an answer saved for another company, or one the site kept) is still an open
// question: it says whose job the text was written for, flags another company's name in it, and offers Regenerate.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const MIN_CHARS = 200;                         // a text box that takes this much is a written answer
  const NOT_OPEN = /\b(salary|compensation|desired pay|pay (expectation|range|requirement)s?|hourly rate|dates?|url|link|web ?site|portfolio|linked ?in|github|e-?mail|phone)\b/i;
  const YES_NO = /^(are|do|did|have|has|will|would|can|could|is|were)\s+you\b/i;
  const ASKS_MORE = /\b(why|how|describe|explain|tell us|share|example|what (makes|interests|excites|draws))\b/i;
  const WAIT_MS = { writing: 1000, limit: 60000 };   // GOAL30 §5: every second while Claude writes; slowly while paused
  const MAX_POLLS = 400;
  // every state is an icon + words (readable with red-green colour blindness): never a colour alone
  const LOOK = { writing: "⟳", ready: "✓", limit: "⏸", login: "⚠", failed: "✕" };

  function isOpen(row) {
    if (!row || !row.label || row.label.length < 8) return false;
    const big = row.kind === "textarea" || (row.kind === "text" && Number(row.max) >= MIN_CHARS);
    if (!big || row.key || row.group) return false;                            // the classifier knows it (cover letter too)
    if (NOT_OPEN.test(row.label)) return false;
    return !(YES_NO.test(row.label) && !ASKS_MORE.test(row.label));
  }

  // the questions of these rows to look up in the cache (once each per job)
  function toLookUp(rows, asked) {
    return [...new Set((rows || []).filter((r) => isOpen(r) && !r.value).map((r) => r.label))].filter((q) => !asked.has(q));
  }
  // GOAL30 §2: open questions already holding text, to ask Job Hunt Agent whose job it was written for (once per text)
  function filledToCheck(rows, asked) {
    const out = {};
    for (const r of rows || []) if (isOpen(r) && r.value && !asked.has(`${r.label}\u0000${r.value}`)) out[r.label] = String(r.value).slice(0, 6000);
    return out;
  }

  const waitFor = (view) => (view && WAIT_MS[view.state]) || 0;     // 0 = done (ready, failed, no login)

  function words(view) {
    if (view.state === "failed") return `No answer: ${String(view.error || "it did not pass the checks").replace(/^No answer that passes the checks: /, "")}`;
    return view.label;
  }

  // the answer under its row: status, the text, and Copy / Fill this field / Regenerate / Shorter / Save to Answers
  function block(view, h, el, btn) {
    const head = el("div", { class: `ans-st ${view.state}`, role: "status" }, el("span", { "aria-hidden": "true" }, LOOK[view.state] || "·"), words(view));
    if (view.state !== "ready") {
      const again = ["failed", "login"].includes(view.state) ? btn("Try again", (e) => h.generate("", e), "link", "Ask Claude again") : null;
      return el("div", { class: "ans" }, head, again);
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
  function filledBlock(r, flags, h, el, btn) {
    const written = (flags && flags.label) || (r.origin ? `Written for ${r.origin.company}${r.origin.title ? `, ${r.origin.title}` : ""}` : "");
    return el("div", { class: "ans" },
      written ? el("div", { class: "ans-st origin" }, el("span", { "aria-hidden": "true" }, "↺"), written) : null,
      flags && flags.names ? el("div", { class: "ans-st names", role: "status" }, el("span", { "aria-hidden": "true" }, "▲"), `Names ${flags.names}`) : null,
      el("div", { class: "btns" }, btn("Regenerate for this job", (e) => h.generate("regenerate", e), "btn", "Claude writes it again for this job, from the same facts"),
        btn("Copy", h.copy, "link"), btn("Fill this field", h.fill, "link", "Puts it into the page's box (you still click Next yourself)"),
        btn("Shorter", (e) => h.generate("shorter", e), "link", "The same answer, shorter"),
        btn("Save to Answers", h.save, "link", "Keep it in Job Hunt Agent › Answers for the same question elsewhere")));
  }

  JH.answers = { isOpen, toLookUp, filledToCheck, filledBlock, waitFor, words, block, LOOK, MAX_POLLS, MIN_CHARS };
  if (typeof module !== "undefined") module.exports = JH.answers;
})();

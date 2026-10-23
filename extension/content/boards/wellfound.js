// Job Hunt Agent Autofill board: Wellfound (formerly AngelList Talent). GOAL27: the apply box asks a few Yes / No questions,
// when the user can start and a note to the company; the note is a long answer (offered in the panel, never filled).
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "wellfound", name: "Wellfound",
  match: (url) => /(^|\.)(wellfound\.com|angel\.co)$/i.test(new URL(url).hostname),
  root: () => document.querySelector("[role=dialog] form, form, main") || document,
  resume: "input[type=file]",
  uploadWaitMs: 2000,
  confirmed: () => /application (sent|submitted)|you applied|thank(s| you) for applying/i.test(JH.text(document.body).slice(0, 6000)),
});

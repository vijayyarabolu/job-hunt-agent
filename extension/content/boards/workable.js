// Job Hunt Agent Autofill board: Workable. Found by its address; the shared filler does the rest (labels, choices, resume).
// Workable reads the resume after upload.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "workable", name: "Workable",
  match: (url) => /(^|\.)workable\.com$/i.test(new URL(url).hostname),
  root: () => document.querySelector("form") || document,
  resume: "input[type=file]",
  uploadWaitMs: 3000,
  confirmed: () => /application (has been )?(submitted|received|sent)|thank(s| you) for applying/i.test(JH.text(document.body).slice(0, 6000)),
});

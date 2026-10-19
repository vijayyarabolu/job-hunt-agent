// Job Hunt Agent Autofill board: Taleo. Found by its address; the shared filler does the rest (labels, choices, resume).
// Taleo: multi-page; each page is a new document.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "taleo", name: "Taleo",
  match: (url) => /(^|\.)taleo\.net$/i.test(new URL(url).hostname),
  root: () => document.querySelector("form") || document,
  resume: "input[type=file]",
  uploadWaitMs: 2000,
  confirmed: () => /thank you for (applying|submitting)|application (has been )?submitted|submission (is )?complete/i.test(JH.text(document.body).slice(0, 6000)),
});

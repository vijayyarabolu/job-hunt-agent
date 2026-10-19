// Job Hunt Agent Autofill board: Jobvite. Found by its address; the shared filler does the rest (labels, choices, resume).
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "jobvite", name: "Jobvite",
  match: (url) => /(^|\.)jobvite\.com$/i.test(new URL(url).hostname),
  root: () => document.querySelector("form") || document,
  resume: "input[type=file]",
  uploadWaitMs: 2000,
  confirmed: () => /thank you for (applying|your interest)|application (has been )?submitted/i.test(JH.text(document.body).slice(0, 6000)),
});

// Job Hunt Agent Autofill board: Paylocity. Found by its address; the shared filler does the rest (labels, choices, resume).
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "paylocity", name: "Paylocity",
  match: (url) => /(^|\.)paylocity\.com$/i.test(new URL(url).hostname),
  root: () => document.querySelector("form") || document,
  resume: "input[type=file]",
  uploadWaitMs: 1500,
  confirmed: () => /thank you for (applying|your application)|application (has been )?(submitted|received)/i.test(JH.text(document.body).slice(0, 6000)),
});

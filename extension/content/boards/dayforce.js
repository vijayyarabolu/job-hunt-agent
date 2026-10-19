// Job Hunt Agent Autofill board: Dayforce. Found by its address; the shared filler does the rest (labels, choices, resume).
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "dayforce", name: "Dayforce",
  match: (url) => /(^|\.)dayforcehcm\.com$/i.test(new URL(url).hostname),
  root: () => document.querySelector("form") || document,
  resume: "input[type=file]",
  uploadWaitMs: 2000,
  confirmed: () => /thank you for (applying|your application)|application (has been )?(submitted|received)/i.test(JH.text(document.body).slice(0, 6000)),
});

// Job Hunt Agent Autofill board: Oracle Cloud HCM. Found by its address; the shared filler does the rest (labels, choices, resume).
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "oracle", name: "Oracle Cloud HCM",
  match: (url) => /(^|\.)oraclecloud\.com$/i.test(new URL(url).hostname),
  root: () => document.querySelector("form, [class*=apply-flow]") || document,
  resume: "input[type=file]",
  uploadWaitMs: 2500,
  confirmed: () => /thank you for (applying|your application)|application (has been )?submitted/i.test(JH.text(document.body).slice(0, 6000)),
});

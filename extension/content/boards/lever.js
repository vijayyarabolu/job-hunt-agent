// Job Hunt Agent Autofill board: Lever. Found by its address; the shared filler does the rest (labels, choices, resume).
// Lever reads the resume after upload and fills name / email itself: waited for.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "lever", name: "Lever",
  match: (url) => /(^|\.)lever\.co$/i.test(new URL(url).hostname),
  root: () => document.querySelector(".application-form, form#application-form") || document,
  resume: "input[type=file][name=resume], input[type=file]#resume-upload-input",
  uploadWaitMs: 4000,
  confirmed: () => /application submitted|thank(s| you) for (applying|your application)|we.ve received your application/i.test(JH.text(document.body).slice(0, 6000)),
});

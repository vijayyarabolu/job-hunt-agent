// Job Hunt Agent Autofill board: Ashby. Found by its address; the shared filler does the rest (labels, choices, resume).
// Ashby fills fields from the uploaded resume; those are kept, the rest filled.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "ashby", name: "Ashby",
  match: (url) => /(^|\.)ashbyhq\.com$/i.test(new URL(url).hostname),
  root: () => document.querySelector("[class*=ashby-application-form-container]") || document,
  resume: "input[type=file][id*=resume i], input[type=file]",
  uploadWaitMs: 4000,
  confirmed: () => /thank(s| you) for (applying|your application)|application (was |has been )?submitted|successfully submitted/i.test(JH.text(document.body).slice(0, 6000)),
});

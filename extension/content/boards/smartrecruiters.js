// Job Hunt Agent Autofill board: SmartRecruiters. Found by its address; the shared filler does the rest (labels, choices, resume).
// SmartRecruiters' form lives in web components (shadow DOM): searched through.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "smartrecruiters", name: "SmartRecruiters",
  match: (url) => /(^|\.)smartrecruiters\.com$/i.test(new URL(url).hostname),
  root: () => document.querySelector("oc-oneclick-form, form, spl-form") || document,
  resume: "input[type=file]",
  uploadWaitMs: 3000,
  confirmed: () => /thank you for (applying|your application)|application (has been )?(successfully )?(sent|submitted)/i.test(JH.text(document.body).slice(0, 6000)),
});

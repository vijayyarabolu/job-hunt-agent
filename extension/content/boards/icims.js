// Job Hunt Agent Autofill board: iCIMS. Found by its address; the shared filler does the rest (labels, choices, resume).
// iCIMS runs its pages inside a frame; each step is a new page (auto-fill each new page).
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "icims", name: "iCIMS",
  match: (url) => /(^|\.)icims\.com$/i.test(new URL(url).hostname),
  root: () => document.querySelector("#iCIMS_MainWrapper, form") || document,
  resume: "input[type=file]",
  uploadWaitMs: 2000,
  confirmed: () => /thank you for (applying|your interest)|application (has been )?(submitted|received)|successfully submitted/i.test(JH.text(document.body).slice(0, 6000)),
});

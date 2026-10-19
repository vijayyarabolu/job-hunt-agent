// Job Hunt Agent Autofill board: Greenhouse. Found by its address; the shared filler does the rest (labels, choices, resume).
// Greenhouse: the company page may hold the form in a frame; the frame fills itself.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "greenhouse", name: "Greenhouse",
  match: (url) => /(^|\.)greenhouse\.io$/i.test(new URL(url).hostname),
  root: () => document.querySelector("#application-form, #application_form, form[action*='greenhouse']") || document,
  resume: "input[type=file]#resume, input[type=file][name*=resume i], input[type=file][id*=resume i]",
  uploadWaitMs: 1500,
  confirmed: () => /thank you for applying|application (has been )?(received|submitted)|we.ve received your application/i.test(JH.text(document.body).slice(0, 6000)),
});

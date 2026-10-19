// Job Hunt Agent Autofill board: BambooHR. Found by its address; the shared filler does the rest (labels, choices, resume).
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "bamboohr", name: "BambooHR",
  match: (url) => /(^|\.)bamboohr\.com$/i.test(new URL(url).hostname),
  root: () => document.querySelector("form") || document,
  resume: "input[type=file][name*=resume i], input[type=file]",
  uploadWaitMs: 1500,
  confirmed: () => /application (has been )?(submitted|received)|thank(s| you) for applying/i.test(JH.text(document.body).slice(0, 6000)),
});

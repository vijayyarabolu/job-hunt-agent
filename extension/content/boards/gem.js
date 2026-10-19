// Job Hunt Agent Autofill board: Gem. Found by its address; the shared filler does the rest (labels, choices, resume).
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "gem", name: "Gem",
  match: (url) => /(^|\.)gem\.com$/i.test(new URL(url).hostname),
  root: () => document.querySelector("form") || document,
  resume: "input[type=file]",
  uploadWaitMs: 2000,
  confirmed: () => /application (has been )?(submitted|received)|thank(s| you) for applying/i.test(JH.text(document.body).slice(0, 6000)),
});

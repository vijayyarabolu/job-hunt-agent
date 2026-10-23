// Job Hunt Agent Autofill board: Dover (app.dover.com/apply). GOAL27: found by its address; its Yes / No buttons and
// controlled React boxes are the shared engine's (buttons kind, checked-and-retried typing).
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "dover", name: "Dover",
  match: (url) => /(^|\.)dover\.(com|io)$/i.test(new URL(url).hostname),
  root: () => document.querySelector("form, main") || document,
  resume: "input[type=file]",
  uploadWaitMs: 2500,
  confirmed: () => /thank(s| you) for (applying|your application)|application (has been )?(received|submitted)/i.test(JH.text(document.body).slice(0, 6000)),
});

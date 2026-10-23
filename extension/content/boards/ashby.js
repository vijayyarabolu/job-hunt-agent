// Job Hunt Agent Autofill board: Ashby. Found by its address; the shared filler does the rest (labels, choices, resume).
// Ashby fills fields from the uploaded resume; those are kept, the rest filled.
// GOAL31 (2026-10-21, 1.6.0 found 8 fields and no EEO): the U.S. EEO questions are a second form, inside
// div.ashby-survey-form-container after the application's; every form is read, never "Autofill from resume" above them.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "ashby", name: "Ashby",
  match: (url) => /(^|\.)ashbyhq\.com$/i.test(new URL(url).hostname),
  root: () => {
    const sel = "[class*=ashby-application-form-container]";
    const forms = [...document.querySelectorAll(sel)].filter((f) => !(f.parentElement && f.parentElement.closest(sel)));
    return forms.length ? forms : document;
  },
  resume: "input[type=file][id*=resume i], input[type=file]",
  uploadWaitMs: 4000,
  confirmed: () => /thank(s| you) for (applying|your application)|application (was |has been )?submitted|successfully submitted/i.test(JH.text(document.body).slice(0, 6000)),
});

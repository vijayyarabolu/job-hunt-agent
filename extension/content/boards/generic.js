// Job Hunt Agent Autofill board: any other site. Labels, names, placeholders and ARIA say what each field asks; the
// panel shows on pages that look like an application (a resume upload or the toolbar button).
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});
JH.boards = JH.boards || [];
JH.boards.push({
  id: "generic", name: "This site",
  match: () => true,
  root: () => document,
  resume: "input[type=file][name*=resume i], input[type=file][id*=resume i], input[type=file][name*=cv i], input[type=file][accept*=pdf i], input[type=file]",
  uploadWaitMs: 1500,
  confirmed: () => /thank(s| you) for (applying|your application|submitting)|application (has been |was )?(successfully )?(submitted|received|sent)|we('ve| have) received your application/i
    .test(JH.text(document.body).slice(0, 6000)),
});

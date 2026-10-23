// GOAL8 UI checks in headless Chrome against a throwaway DB and a fake resumegen (tests/test_goal8_ui.py):
// Generate resume / multi-select / g, live chips, the resume panel, "Did you apply?" (Yes + form, No, Later, several,
// app re-open), outside jobs under Saved, Settings, the years labels, "Resume app is offline", the phone sheet.
// usage: node goal8_ui.mjs <base url> <token> <chrome path> <profile dir> <fake resumegen url>
import { sleep, start } from "./cdp.mjs";
import { settingsPage } from "./settings_page.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, FAKE] = process.argv.slice(2);
const { send, js, until, key, errors, checks, check, click, clickText, server, close } = await start(CHROME, PROFILE_DIR);
const settings = settingsPage({ js, until, clickText });
const fake = async (path, body) => (await fetch(`${FAKE}${path}`, body ? { method: "POST", body: JSON.stringify(body) } : {})).json();
const postsAt = async (n) => { for (let i = 0; i < 50; i++) { const s = await fake("/__state"); if (s.posts.length >= n) return s; await sleep(100); } return fake("/__state"); };   // the chip is optimistic, the POST lands just after
const P = "tester";
const rows = () => js("return document.querySelectorAll('#list .row[data-i]').length");
const chipText = (i) => js(`return document.querySelector('#job-${i} .rgc')?.textContent || ''`);
const dialogText = () => js("return document.querySelector('dialog.back-dlg[open]')?.textContent || ''");
// coming back: Apply ↗ opened the posting in a new tab (this page went hidden); bring this tab to the front again
const back = async () => { await send("Page.bringToFront"); await sleep(200); await js("document.dispatchEvent(new Event('visibilitychange'))"); };
let loads = 0;
const load = async (hash) => {                      // a new query string: a full page load, not a same-document hop
  await send("Page.navigate", { url: `${BASE}/?n=${++loads}#${hash}` });
  await sleep(300);
  await until("document.querySelector('#list-head h1')?.textContent && !document.querySelector('#list[aria-busy]')", 8000);
};

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:${P}` });
  check("feed loads", await until("document.querySelectorAll('#list .row[data-i]').length >= 4", 8000), await rows());
  check("the resume app connects after the first paint", await until("document.body.classList.contains('rg-on')", 8000));
  const uids = (await server(`/api/jobs?profile=${P}&tab=recommended`)).jobs.map((j) => j.uid);

  // ---- §4 labels ----
  const tags = await js("return [...document.querySelectorAll('#list .yrs')].map((t) => t.textContent)");
  check("each job says what years were read", tags.includes("Asks 3 yrs") && tags.includes("years not stated"), tags);

  // ---- §1 Generate resume, multi-select, g ----
  await until("[...document.querySelectorAll('#detail .acts .btn')].some((b) => b.textContent === 'Generate resume')");
  check("the detail pane has Generate resume", await clickText("#detail .acts .btn", "Generate resume"));
  check("its chip shows queued at once", await until("document.querySelector('#job-0 .rgc')?.textContent === 'Resume queued'"), await chipText(0));
  check("the detail button then says Generate again", await until("[...document.querySelectorAll('#detail .acts .btn')].some((b) => b.textContent === 'Generate again')"));
  let st = await postsAt(1);
  check("one click sends one job to the resume app", st.posts.length === 1 && st.posts[0][0].source_job_id === uids[0]
    && st.posts[0][0].jd_text.length > 80 && st.posts[0][0].profile === P, JSON.stringify(st.posts).slice(0, 300));

  await js("document.querySelector('#list').focus()");
  await key("j");
  await key(" ");
  check("Space selects a job", await js("return document.querySelector('#job-1').getAttribute('aria-checked') === 'true'"));
  await js("document.querySelector('#job-3').dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }))");
  const picked = await js("return [...document.querySelectorAll('#list .row[aria-checked=true]')].map((r) => r.id)");
  check("Shift-click selects the range", picked.join() === "job-1,job-2,job-3", picked);
  check("the bar counts them", (await js("return document.querySelector('.pickbar:not([hidden]) .pb-n')?.textContent")) === "3 selected");
  check("the bar has no stray text", !(await js("return document.querySelector('.pickbar').textContent")).includes("null"),
    await js("return document.querySelector('.pickbar').textContent"));
  check("Generate for selected", await clickText(".pickbar .btn", "Generate for selected"));
  await until("document.querySelector('#job-3 .rgc')?.textContent === 'Resume queued'");
  st = await postsAt(2);
  check("it queues them in one request", st.posts.length === 2 && st.posts[1].length === 3, st.posts.map((p) => p.length));
  check("the selection clears", await js("return !document.querySelector('#list .row[aria-checked=true]') && document.querySelector('.pickbar').hidden"));
  await key("k");
  await key("g");
  check("g on a queued job says so", await until("[...document.querySelectorAll('.toast p')].some((p) => p.textContent.includes('already in the queue'))"));
  const free = await js("const b = document.querySelector('#list .row .rg-gen'); return b ? [b.closest('.row').id, b.textContent] : null");
  check("a feed row without a resume has a Generate resume button", free && free[1] === "Generate resume", free);
  await js(`document.querySelector('#${free?.[0]} .rg-gen').click()`);
  check("the row button queues that job", await until(`document.querySelector('#${free?.[0]} .rgc')?.textContent === 'Resume queued'`));
  st = await postsAt(3);
  check("in one request of one job", st.posts.length === 3 && st.posts[2].length === 1, st.posts.map((p) => p.length));

  // ---- live chip + the panel ----
  const rgId = Object.values(st.jobs).find((j) => j.source_job_id === uids[0])?.id;
  await fake("/__set", { id: rgId, status: "ready", attempts: 1, elapsed_s: 240, drive_file_id: "F1", drive_link: "https://drive.example/F1",
    drive_name: "Tess_Tester_ML_Resume(Acme).pdf",
    result: { base: { id: "ml", label: "ML Engineer" }, ats: { base_pct: 60, tailored_pct: 74, label: "Greenhouse estimate", missing: ["gRPC"] },
      files: { pdf: "/x.pdf", png: "/x.png" }, approved: { link: "https://drive.example/F1", name: "Tess_Tester_ML_Resume(Acme).pdf" },
      diff: { changed_bullets: 1, total_bullets: 5, summary: { changed: true, ops: [["equal", "ML Engineer with", "ML Engineer with"], ["replace", "4 years", "4 years building"]] } } } });
  check("the chip turns ready by itself", await until("['Resume ready', 'Resume approved'].includes(document.querySelector('#job-0 .rgc')?.textContent)", 15000), await chipText(0));   // ~3 polls (rg.js FAST_MS 4 s) under load
  await js("document.querySelector('#job-0 .rgc').click()");
  check("the chip opens the resume panel", await until("[...document.querySelectorAll('#detail h2')].some((x) => x.textContent === 'Tailored resume') && document.querySelector('#detail .rg-status')"));   // it opens at once, then fills
  const panel = await js("return document.querySelector('#detail').textContent");
  check("it shows ATS before and after", panel.includes("60%") && panel.includes("74%"), panel.slice(0, 300));
  check("the PDF is shown inline", await js("return document.querySelector('#detail iframe.rg-pdf')?.src.includes('which=pdf')"));
  check("the PDF passes through job-hunt-agent", (await js("return (await fetch(document.querySelector('#detail iframe.rg-pdf').src)).headers.get('content-type')")) === "application/pdf");
  check("the diff marks the changes", await js("return !!document.querySelector('#detail .rg-diff ins') && !!document.querySelector('#detail .rg-diff del')"));
  check("Regenerate takes a note", await js("const i = document.querySelector('#detail .rg-note'); i.value = 'more MLOps'; return true")
    && await clickText("#detail .btn", "Start over"));
  await sleep(600);
  st = await fake("/__state");
  check("the resume app gets the note", st.actions.some(([id, a, b]) => id === rgId && a === "regenerate" && b.note === "more MLOps"), JSON.stringify(st.actions));
  await fake("/__set", { id: rgId, status: "ready" });
  await key("Escape");

  // ---- §2 Did you apply? ----
  await js("document.querySelector('#list').focus()");
  await key("a");                                     // Apply ↗ on job 0 (opens the posting in a new tab)
  await sleep(400);
  await key("7");                                     // a job on the open list is asked on its row; off it, here
  await back();
  check("coming back asks Did you apply?", await until("document.querySelector('dialog.back-dlg[open]')"), await dialogText());
  check("it names the company and role", (await dialogText()).includes("Did you apply to Acme · "), await dialogText());
  await clickText("dialog.back-dlg .btn", "Yes");
  check("Yes marks applied and attaches the tailored resume", await until("document.querySelector('dialog.back-dlg[open]')?.textContent.includes('Tailored resume attached')", 8000), await dialogText());
  const fields = await js("return [...document.querySelectorAll('dialog.back-dlg label.field > span')].map((s) => s.textContent)");
  check("the form lists only the empty sheet columns", fields.includes("Target Name") && !fields.includes("Company Name")
    && !fields.includes("Date Applied") && !fields.includes("Resume"), fields);
  await js("const i = [...document.querySelectorAll('dialog.back-dlg label.field')].find((l) => l.textContent.startsWith('Target Name')).querySelector('input'); i.value = 'Pat Doe'");
  await clickText("dialog.back-dlg .btn", "Save");
  await until("!document.querySelector('dialog.back-dlg[open]')");
  const app0 = (await server(`/api/apply_options?profile=${P}&uid=${encodeURIComponent(uids[0])}`)).app;
  const row0 = await server(`/api/tracker/row?profile=${P}&app=${app0.id}`);
  check("the row is applied today with what the form said", row0.status === "applied" && row0.target_name === "Pat Doe" && !!row0.date_applied,
    JSON.stringify({ s: row0.status, t: row0.target_name, d: row0.date_applied }));

  await key("1");                                      // back to the feed from Follow-ups
  await until("document.querySelectorAll('#list .row[data-i]').length >= 4 && !document.querySelector('#list[aria-busy]')", 8000);
  await js("document.querySelector('#list').focus()");
  await key("j");
  await key("a");                                      // job 1, then job 2: newest first
  await key("j");
  await key("a");
  await sleep(300);
  await key("7");
  await send("Page.bringToFront");
  await js("window.dispatchEvent(new Event('focus'))");
  check("several are asked newest first", await until("document.querySelector('dialog.back-dlg[open]')?.textContent.includes('1 of 2')"), await dialogText());
  const firstAsked = await dialogText();
  await clickText("dialog.back-dlg .btn", "No");
  check("then the next one", await until("document.querySelector('dialog.back-dlg[open]')?.textContent.includes('2 of 2')"), await dialogText());
  await clickText("dialog.back-dlg .btn", "Later");
  await until("!document.querySelector('dialog.back-dlg[open]')");
  const pend = await server(`/api/pending?profile=${P}&boot=1`);
  check("No closes it and Later snoozes it", pend.snoozed === 1 && pend.ask.length === 1, JSON.stringify(pend));
  check("No leaves the job unapplied", !(await server(`/api/jobs?profile=${P}&tab=recommended`)).jobs.some((j) => firstAsked.includes(j.title) && j.app?.status === "applied"));
  check("Later puts a badge on Follow-ups", (await js("return document.querySelector('#tabs [data-tab=followups] .n').textContent")) === "1");
  await back();
  await sleep(1200);
  check("coming back does not ask a Later one again", !(await js("return !!document.querySelector('dialog.back-dlg[open]')")));
  await send("Page.reload");
  // the app opens on Recommended: the job is on the list, so it is asked on its row, else in the dialog
  check("opening the app asks it again", await until("document.querySelector('dialog.back-dlg[open], #list .ask-in')", 8000), await dialogText());
  if (await js("return !!document.querySelector('#list .ask-in')")) await clickText("#list .ask-in .mini", "Not yet");
  else await clickText("dialog.back-dlg .btn", "No");
  await until("!document.querySelector('dialog.back-dlg[open], #list .ask-in')");

  // ---- §3 outside job ----
  await fake("/__add", { profile: P, company: "Recruiter Co", role: "AI Engineer", jd_text: "Requirements: Python, PyTorch, Kubernetes. ".repeat(5),
    jd_url: "http://127.0.0.1:9/post", status: "ready" });
  await load(`recommended:${P}`);                    // resumegen GOAL3 §7: an open starts on Recommended; click Saved
  await js("document.querySelector('#tabs [data-tab=saved]').click()");
  await until("location.hash === '#saved' && !document.querySelector('#list[aria-busy]')", 8000);
  check("an outside job shows under Saved", await until("[...document.querySelectorAll('.section-head h2')].some((x) => x.textContent.startsWith('Outside jobs (1)'))", 12000),
    await js("return JSON.stringify({ tab: document.body.dataset.view, g8: !!window.S, more: document.querySelector('#list-more')?.textContent.slice(0, 200), head: document.querySelector('#list-head')?.textContent.slice(0, 100) })"));
  check("with its resume chip", await until("document.querySelector('.out-row .rgc')?.textContent === 'Resume ready'", 6000));
  await clickText(".out-row .btn", "Mark applied");
  check("Mark applied works for it", await until("document.querySelector('dialog.back-dlg[open]')?.textContent.includes('Applied: Recruiter Co · AI Engineer')", 8000), await dialogText());
  await clickText("dialog.back-dlg .btn", "Skip");
  const outsideRow = (await server(`/api/tracker?profile=${P}&q=Recruiter`)).rows?.[0];
  check("its row is applied", outsideRow?.status === "applied", JSON.stringify(outsideRow || {}).slice(0, 200));

  // ---- Settings (years cap) ----
  check("Settings shows the years cap", (await settings.read("filters", "Hide jobs that ask for more")) === "4");
  await settings.set("filters", "Hide jobs that ask for more", "3");
  await settings.close();
  check("it is saved to the profile", (await server(`/api/settings?profile=${P}`)).max_required_years === 3);

  // ---- GOAL33 §3d lag: Generate again on a job that has a resume says Queued in the click itself ----
  await load(`recommended:${P}`);
  await until("document.body.classList.contains('rg-on') && document.querySelector('#list .rgc.rg-ready')", 10000);
  await js("document.querySelector('#list .rgc.rg-ready').closest('.row').querySelector('.row-title, h3, .t')?.click() || document.querySelector('#list .rgc.rg-ready').closest('.row').click()");
  await until("[...document.querySelectorAll('#detail .rg-gen-label')].some((b) => b.textContent === 'Generate again')", 8000);
  const again = await js(`const row = document.querySelector('#list .row[aria-selected=true], #list .row.sel, #list .row.on') || document.querySelector('#list .rgc.rg-ready').closest('.row');
    const uid = document.querySelector('#detail .rg-gen-label').dataset.uid, chip = document.querySelector('#list .rgc[data-uid="' + uid + '"]');
    const before = chip?.textContent; document.querySelector('#detail .rg-gen-label').click(); return [before, chip?.textContent]`);
  check("Generate again shows Queued at once", again[1] === "Resume queued", again);
  await sleep(800);

  // ---- phone sheet ----
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await load(`recommended:${P}`);
  await until("document.body.classList.contains('rg-on')", 8000);
  await js("document.querySelector('#job-0').click()");
  check("the phone sheet has the resume actions", await until("[...document.querySelectorAll('#detail .acts .btn, #detail .acts .rgc')].some((b) => b.textContent.startsWith('Generate'))"));
  await key("Escape");
  await until("document.querySelector('.sel-mode')");
  await clickText(".sel-mode", "Select");
  await until("document.body.classList.contains('pick-mode')");
  const box = await js("const r = document.querySelector('#job-0 .pick').getBoundingClientRect(); return [r.width, r.height]");
  check("Select shows the checkboxes on the phone", box[0] >= 22, box);

  // ---- offline ----
  await fake("/__down", {});
  await js(`await fetch("/api/resumes?profile=${P}")`);             // GOAL30-app §0: one failed call is not offline yet
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await load(`recommended:${P}`);
  check("job-hunt-agent keeps working when the resume app is down", (await rows()) >= 3);
  check("it says Resume app is offline", await until("document.querySelector('#detail')?.textContent.includes('Resume app is offline')", 8000));
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || err);
} finally {
  console.log(JSON.stringify({ checks }));
  close();
}

// GOAL19 §2-§4 in headless Chrome (tests/test_goal19_lists_ui.py): Not interested → "Other…" with a text box;
// × Remove / Shift+X with Undo on every list; duplicates marked; the resume panel after Re-import / Undo my edit / Cancel.
// usage: node goal19_lists.mjs <base url> <token> <chrome path> <profile dir> <json: pid, rg, rg_id, dup_old, dup_new>
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, WANT] = process.argv.slice(2);
const want = JSON.parse(WANT);
const P = want.pid;
const { send, js, until, key, errors, checks, check, click, clickText, server, close } = await start(CHROME, PROFILE_DIR);
const S = JSON.stringify;
const ROWS = "#list .row[data-i]";
const rowCount = () => js(`return document.querySelectorAll('${ROWS}').length`);
const rowWith = (text) => `[...document.querySelectorAll('${ROWS}')].find((r) => r.textContent.includes(${S(text)}))`;
const toastText = () => js("return [...document.querySelectorAll('.toast')].map((t) => t.textContent).join(' | ')");
// a resume button stays busy (disabled) until the new file is on screen; press it once it is free, as a
// person would (under a full test run the PDF frame can load late)
const press = async (sel, text) => {
  await until(`[...document.querySelectorAll(${JSON.stringify(sel)})].some((x) => x.textContent.trim().startsWith(${JSON.stringify(text)}) && !x.disabled)`, 15000);
  return clickText(sel, text);
};
const undoToast = (text) => js(`const t = [...document.querySelectorAll('.toast')].find((x) => x.textContent.includes(${S(text)})); t?.querySelector('.undo')?.click(); return !!t`);
const rg = async (path, body) => (await fetch(want.rg + path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: S(body) } : {})).json();
const feedUids = async (tab = "recommended") => (await server(`/api/jobs?profile=${P}&tab=${tab}`)).jobs.map((j) => j.uid);
const open = async (hash) => {
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}&n=${Math.random()}#${hash}:${P}` });
  await until("document.querySelector('#tabs [aria-selected=true]')", 10000);
  await sleep(1500);
};
const toTab = async (t) => { await js(`[...document.querySelectorAll('[data-tab=${t}]')].find((b) => b.offsetParent)?.click(); return 1`); await sleep(900); };
const rgSent = async (name, from = 0, ms = 4000) => { for (let t = 0; t < ms; t += 200) { if ((await rg("/__state")).actions.slice(from).some((a) => a[1] === name)) return true; await sleep(200); } return false; };
const chipsInToast = () => js("return [...document.querySelectorAll('.toast .why .chip')].map((c) => c.textContent)");

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await open("recommended");
  await until(`document.querySelectorAll('${ROWS}').length >= 4`, 10000);

  // ---- §3 duplicates: marked, counted, × removes the older copy ----
  const dupTag = await js(`return [...document.querySelectorAll('${ROWS} .tag')].map((t) => t.textContent).find((t) => t.startsWith('Duplicate of')) || ''`);
  check("a duplicate is marked Duplicate of …", dupTag === "Duplicate of Acme · Machine Learning Engineer, Search", dupTag + " " + S(await js(`return (await (await fetch('/api/jobs?profile=${P}&tab=recommended')).text()).slice(0, 300)`)) + " " + await js("return location.href + ' ' + document.querySelector('#list').textContent.slice(0, 200)"));
  check("the list head counts the duplicates", (await js("return document.querySelector('#list-head').textContent")).includes("1 duplicate: show"),
    await js("return document.querySelector('#list-head').textContent"));
  // the tag stays inside its column (it ran over "Priority"); "Duplicates only" ends when the list changes
  check("the duplicate tag is clipped to its column", await js("const t = document.querySelector('.dup-tag'); return !!t && getComputedStyle(t).textOverflow === 'ellipsis' && t.getBoundingClientRect().right <= t.closest('.row-main').getBoundingClientRect().right + 1"));
  await js("[...document.querySelectorAll('#list-head .lh-dupes button')][0].click(); return 1");
  await until("document.querySelector('#list-head')?.textContent.includes('Show every job')", 6000);
  await js("document.querySelector('#tabs [data-tab=saved]').click(); return 1");
  await until("location.hash.startsWith('#saved')", 4000);
  await js("document.querySelector('#tabs [data-tab=recommended]').click(); return 1");
  check("Duplicates only ends when another list opens", await until("location.hash.startsWith('#recommended') && document.querySelector('#list-head')?.textContent.includes('1 duplicate: show')", 8000),
    await js("return document.querySelector('#list-head')?.textContent"));
  await until(`document.querySelectorAll('${ROWS}').length > 1`, 6000);
  const n0 = await rowCount();
  const xTip = await js(`return document.querySelector('${ROWS} .x-rm')?.title`);
  check("every feed row has × with its tooltip", xTip === "Remove: a duplicate or a job you never want to see. It leaves every list, Archived too; Undo for 8 s (Shift+X)" && (await js(`return [...document.querySelectorAll('${ROWS}')].every((r) => r.querySelector('.x-rm'))`)), xTip);
  await js(`[...document.querySelectorAll('${ROWS}')].find((r) => r.querySelector('.tag.warn')?.textContent.startsWith('Duplicate'))?.querySelector('.x-rm').click(); return 1`);
  check("× takes the row out at once", await until(`document.querySelectorAll('${ROWS}').length === ${n0 - 1}`, 1000), await rowCount());
  check("an Undo toast says Removed", await until("[...document.querySelectorAll('.toast')].some((t) => t.textContent.startsWith('Removed:') && t.querySelector('.undo'))", 3000), await toastText());
  await sleep(500);
  check("the server hides it", !(await feedUids()).includes(want.dup_old), S(await feedUids()));
  await undoToast("Removed:");
  check("Undo puts it back", await until(`document.querySelectorAll('${ROWS}').length === ${n0}`, 3000), await rowCount());
  await sleep(400);
  check("and the server shows it again", (await feedUids()).includes(want.dup_old));
  await js(`[...document.querySelectorAll('${ROWS}')].find((r) => r.querySelector('.tag.warn')?.textContent.startsWith('Duplicate'))?.querySelector('.x-rm').click(); return 1`);
  await until(`document.querySelectorAll('${ROWS}').length === ${n0 - 1}`, 2000);
  await sleep(700);
  await open("recommended");
  await until(`document.querySelectorAll('${ROWS}').length > 0`, 10000);
  check("a removed job never comes back (reload)", (await rowCount()) === n0 - 1 && !(await feedUids()).includes(want.dup_old), await rowCount());
  check("the copy it duplicated stays", (await feedUids()).includes(want.dup_new), S(await feedUids()));
  check("the duplicate count is gone with it", !(await js("return document.querySelector('#list-head').textContent")).includes("duplicate"));
  for (const tab of ["saved", "archived", "resumes"]) check(`removed stays out of ${tab}`, !(await feedUids(tab)).includes(want.dup_old));

  // ---- §3 Shift+X on the selected row, Undo ----
  const n1 = await rowCount();
  await js("document.querySelector('#list').focus(); return 1");
  await key("j");
  await key("X", 8);
  check("Shift+X removes the selected row", await until(`document.querySelectorAll('${ROWS}').length === ${n1 - 1}`, 1500), await rowCount());
  await sleep(400);
  await undoToast("Removed:");
  check("and Undo brings it back", await until(`document.querySelectorAll('${ROWS}').length === ${n1}`, 3000), await rowCount());
  await sleep(600);

  // ---- §2 Other… in the feed: Esc closes the box, Enter saves; the Archived row and stats show it ----
  await js(`[...document.querySelectorAll('${ROWS}')].find((r) => r.querySelector('.row-title').textContent === 'Machine Learning Engineer').click(); return 1`);
  await sleep(500);
  await js("document.activeElement?.blur(); return 1");
  await key("x");
  check("Not interested asks why with 5 chips, Other… last", await until("document.querySelectorAll('.toast .why .chip').length === 5", 3000)
    && (await chipsInToast()).at(-1) === "Other…", S(await chipsInToast()));
  await js("[...document.querySelectorAll('.toast .why .chip')].at(-1).click(); return 1");
  check("Other… opens a text box with the focus", await until("document.activeElement?.matches('.toast .why input')", 1500));
  await key("Escape");
  check("Esc closes the box and the chips are back", await until("document.querySelectorAll('.toast .why .chip').length === 5 && !document.querySelector('.toast .why input')", 1500));
  await js("[...document.querySelectorAll('.toast .why .chip')].at(-1).click(); return 1");
  await until("document.activeElement?.matches('.toast .why input')", 1500);
  for (const ch of "Not remote") await key(ch);
  await key("Enter");
  check("Enter saves the typed reason", await until("[...document.querySelectorAll('.toast .why')].some((w) => w.textContent.includes('Reason saved: Not remote'))", 3000), await toastText());
  await toTab("archived");
  await until(`document.querySelectorAll('${ROWS}').length > 0`, 8000);
  const tag = await js(`return ${rowWith("Not remote")}?.querySelector('.tag[title="Why you passed on it"]')?.textContent || ''`);
  check("the Archived row shows the typed reason", tag === "Other: Not remote", tag);
  const stats = await server(`/api/tracker/stats?profile=${P}`);
  check("stats count it", (stats.not_interested?.other_notes || []).some((n) => n.note === "Not remote" && n.n === 1) && (stats.html || "").includes("Not remote"), S(stats.not_interested));

  // ---- §3 × on an Archived application row: asks first, Undo ----
  const apps = () => js("return document.querySelectorAll('.apps li .app-row').length");
  const a0 = await apps();
  await js("document.querySelector('.apps li > .x-rm').click(); return 1");
  check("× on an application row asks first", await until("document.querySelector('#ask[open]')?.textContent.includes('Delete Gone Co')", 2000), await js("return document.querySelector('#ask')?.textContent"));
  await js("document.querySelector('#ask-ok').click(); return 1");
  check("the application row is deleted", await until(`document.querySelectorAll('.apps li .app-row').length === ${a0 - 1}`, 4000), await apps());
  await undoToast("Deleted Gone Co");
  check("Undo brings the application back", await until(`document.querySelectorAll('.apps li .app-row').length === ${a0}`, 5000), await apps());

  // ---- §2 in the job panel ----
  await toTab("recommended");
  await until(`document.querySelectorAll('${ROWS}').length > 0`, 8000);
  await js(`document.querySelector('${ROWS}').click(); return 1`);
  await until("document.querySelector('#detail .acts [aria-label^=\"Not interested\"]')", 5000);
  check("the job panel has × Remove too", await js("return document.querySelector('#detail .rm-lbl')?.title === 'Remove: a duplicate or a job you never want to see. It leaves every list, Archived too; Undo for 8 s (Shift+X)'"));
  await js("document.querySelector('#detail .acts [aria-label^=\"Not interested\"]').click(); return 1");
  check("the job panel's Not interested offers Other…", await until("[...document.querySelectorAll('.toast .why .chip')].some((c) => c.textContent === 'Other…')", 3000), S(await chipsInToast()));
  await undoToast("Not interested");
  await sleep(600);

  // ---- §4 the resume panel: the files' time on the URL, what changed, Approve again, Re-import shows the new file ----
  await open("recommended");
  await until(`document.querySelector('${ROWS} .rgc.rg-ready')`, 10000);
  await js(`document.querySelector('${ROWS} .rgc.rg-ready').click(); return 1`);
  check("the resume panel opens", await until("document.querySelector('#detail .rg-acts')", 6000));
  const src0 = await js("return document.querySelector('#detail iframe.rg-pdf')?.src || ''");
  check("the preview URL carries the files' time", src0.includes("v=2026-10-12T21%3A00%3A00Z"), src0);
  check("what changed shows under the preview", (await js("return document.querySelector('#detail .rg-changed')?.textContent")) === "Your note: Summary shorter");
  const btns = () => js("return [...document.querySelectorAll('#detail .rg-acts button')].map((b) => b.textContent)");
  const have = await btns();
  check("the panel's buttons", ["Approve again", "Fix with note", "Undo my edit", "Cancel", "Get my Doc edits"].every((x) => have.includes(x)), S(have));
  await rg("/__set", { id: want.rg_id, files_version: "2026-10-12T21:30:00Z", what_changed: "Your Doc edit: Location on the top line: Phoenix, AZ" });
  await press("#detail .rg-acts button", "Get my Doc edits");
  check("after Re-import the preview is the new file at once", await until("document.querySelector('#detail iframe.rg-pdf')?.src.includes('v=2026-10-12T21%3A30%3A00Z')", 5000),
    await js("return document.querySelector('#detail iframe.rg-pdf')?.src"));
  check("and Download is the new file too", (await js("return [...document.querySelectorAll('#detail a.btn')].find((a) => a.textContent === 'Download PDF')?.href || ''")).includes("v=2026-10-12T21%3A30%3A00Z"));
  check("what changed follows", (await js("return document.querySelector('#detail .rg-changed')?.textContent")) === "Your Doc edit: Location on the top line: Phoenix, AZ");
  await press("#detail .rg-acts button", "Approve again");
  check("Approve again sends the edited version to Drive", await rgSent("approve"));
  await sleep(500);
  await press("#detail .rg-acts button", "Undo my edit");
  check("Undo my edit asks a second press", (await btns()).includes("Yes, undo it"), S(await btns()));
  const before = (await rg("/__state")).actions.length;
  await press("#detail .rg-acts button", "Yes, undo it");
  check("the second press sends Undo my edit", await rgSent("undo_edit", before));
  check("and says so", await until("[...document.querySelectorAll('.toast')].some((t) => t.textContent.startsWith('Back to the version before your'))", 3000), await toastText());
  await sleep(400);
  await press("#detail .rg-acts button", "Cancel");
  check("Cancel asks a second press", (await btns()).includes("Yes, cancel it"), S(await btns()));
  await press("#detail .rg-acts button", "Yes, cancel it");
  check("Cancel drops it with an Undo", await until("[...document.querySelectorAll('.toast')].some((t) => t.textContent.startsWith('Cancelled: moved to Archived') && t.querySelector('.undo'))", 4000), await toastText());
  await undoToast("Cancelled: moved to Archived");
  check("Undo restores it in the Resume app", await rgSent("unarchive"));
  await key("Escape");
  await sleep(300);

  // ---- §4 Fix with note / Looks good / Regenerate: the panel shows the new file when each one is done ----
  const NEEDS = want.needs;
  const pdfV = () => js("return decodeURIComponent((document.querySelector('#detail iframe.rg-pdf')?.src || '').split('v=')[1] || '')");
  const finish = async (version, status = "ready") => rg("/__set", { id: NEEDS, status, files_version: version });
  await js(`const m = await import('/js/rg.js'); m.openResume(${NEEDS}); return 1`);
  await until("document.querySelector('#detail .rg-acts')", 6000);
  await sleep(500);
  check("a needs-you resume lists its problem", (await js("return document.querySelector('#detail .rg-fails')?.textContent || ''")).includes("2 pages"));
  const nb = await btns();
  check("needs you: Looks good, Fix with note, Google Doc, Cancel", ["Accept as it is", "Fix with note", "Open as Google Doc", "Cancel"].every((x) => nb.includes(x)) && !nb.includes("Undo my edit"), S(nb));
  await js("const n = document.querySelector('#detail .rg-note'); n.value = 'only make the summary shorter'; return 1");
  await press("#detail .rg-acts button", "Fix with note");
  check("Fix with note is sent with its note", await rgSent("fix"));
  check("and the panel says it is being fixed", await until("(document.querySelector('#detail .rg-status')?.textContent || '').includes('queued')", 3000));
  await finish("2026-10-12T22:10:00Z", "needs_you");
  check("when the fix is done the panel shows the new file by itself", await until("decodeURIComponent(document.querySelector('#detail iframe.rg-pdf')?.src || '').includes('v=2026-10-12T22:10:00Z')", 15000), await pdfV());
  await rg("/__set", { id: NEEDS, files_version: "2026-10-12T22:20:00Z" });
  await press("#detail .rg-acts button", "Accept as it is");
  check("Looks good is sent", await rgSent("accept"));
  check("after Looks good the panel shows the file as it is now", await until("decodeURIComponent(document.querySelector('#detail iframe.rg-pdf')?.src || '').includes('v=2026-10-12T22:20:00Z')", 5000), await pdfV());
  await finish("2026-10-12T22:20:00Z", "ready");
  await sleep(300);
  await js(`const m = await import('/js/rg.js'); m.openResume(${NEEDS}); return 1`);
  await until("[...document.querySelectorAll('#detail .rg-acts button')].some((b) => b.textContent === 'Start over')", 6000);
  await press("#detail .rg-acts button", "Start over");
  check("Regenerate is sent", await rgSent("regenerate"));
  await finish("2026-10-12T22:30:00Z", "ready");
  check("when Regenerate is done the panel shows the new file by itself", await until("decodeURIComponent(document.querySelector('#detail iframe.rg-pdf')?.src || '').includes('v=2026-10-12T22:30:00Z')", 15000), await pdfV());
  check("and Download is the new file", (await js("return [...document.querySelectorAll('#detail a.btn')].find((a) => a.textContent === 'Download PDF')?.href || ''")).includes("v=2026-10-12T22%3A30%3A00Z"));
  await key("Escape");
  await sleep(300);

  // ---- §2 / §3 in the Resumes queue: Not interested → Other…, and × ----
  await toTab("resumes");
  await until(`[...document.querySelectorAll('${ROWS} .mini')].some((b) => b.textContent === 'Not interested')`, 10000);
  check("the Resumes rows have × too", await js(`return [...document.querySelectorAll('${ROWS}')].every((r) => r.querySelector('.x-rm'))`));
  await js(`[...document.querySelectorAll('${ROWS} .mini')].find((b) => b.textContent === 'Not interested').click(); return 1`);
  check("the Resumes queue's Not interested offers Other…", await until("[...document.querySelectorAll('.toast .why .chip')].some((c) => c.textContent === 'Other…')", 3000), S(await chipsInToast()));
  await undoToast("Not interested");
  await sleep(600);

  // ---- §3 the Tracker grid: the row number's × asks, deletes, Undo ----
  await toTab("tracker");
  await until("document.querySelectorAll('.g tbody tr[data-app]').length > 0", 10000);
  await sleep(400);
  const g0 = await js("return document.querySelectorAll('.g tbody tr[data-app]').length");
  const gx = await js("const b = document.querySelector('.g tbody tr[data-app] .g-x'); return b ? b.title : ''");
  check("each grid row has × on its number", gx.startsWith("Remove: duplicate or not wanted"), gx);
  await js("document.querySelector('.g tbody tr[data-app] .g-x').click(); return 1");
  check("the grid's × asks first", await until("document.querySelector('#ask[open]')", 2000));
  await js("document.querySelector('#ask-ok').click(); return 1");
  check("the grid row is deleted", await until(`document.querySelectorAll('.g tbody tr[data-app]').length === ${g0 - 1}`, 4000));
  await js("[...document.querySelectorAll('.toast')].find((t) => t.textContent.startsWith('Deleted'))?.querySelector('.undo')?.click(); return 1");
  check("Undo puts the grid row back", await until(`document.querySelectorAll('.g tbody tr[data-app]').length === ${g0}`, 5000));
  const rn = await js("const th = document.querySelector('.g tbody tr[data-app] .g-rn'); const r = th.getBoundingClientRect(); return { x: Math.round(r.left + 8), y: Math.round(r.top + r.height / 2) }");
  for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) await send("Input.dispatchMouseEvent", { type, x: rn.x, y: rn.y, button: "left", clickCount: 1 });
  await sleep(200);
  await key("X", 8);
  check("Shift+X on a selected row asks to delete it", await until("document.querySelector('#ask[open]')?.textContent.includes('Delete')", 2000));
  await js("document.querySelector('#ask button[value=no]').click(); return 1");
  await sleep(300);
  check("Cancel keeps the row", (await js("return document.querySelectorAll('.g tbody tr[data-app]').length")) === g0 && !(await js("return !!document.querySelector('.g-ed')")));

  // ---- phone: × on rows and cards (44 px), Other… in the toast ----
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 3, mobile: true });
  await send("Emulation.setTouchEmulationEnabled", { enabled: true });
  await open("recommended");
  await until(`document.querySelectorAll('${ROWS}').length > 0`, 10000);
  const px = await js(`const b = document.querySelector('${ROWS} .x-rm'); const r = b.getBoundingClientRect(); return { h: Math.round(r.height), op: getComputedStyle(b).opacity }`);
  check("phone: × shows on every row, 44 px", px.h >= 44 && px.op === "1", S(px));
  await js(`document.querySelector('${ROWS}').click(); return 1`);
  await until("document.querySelector('#detail .acts [aria-label^=\"Not interested\"]')", 6000);
  await js("document.querySelector('#detail .acts [aria-label^=\"Not interested\"]').click(); return 1");
  await until("document.querySelectorAll('.toast .why .chip').length === 5", 3000);
  await js("[...document.querySelectorAll('.toast .why .chip')].at(-1).click(); return 1");
  check("phone: Other… opens the text box", await until("document.querySelector('.toast .why input')", 1500));
  await undoToast("Not interested");
  await toTab("tracker");
  await until("document.querySelector('.t-card[data-app] .x-rm')", 10000);
  const c0 = await js("return document.querySelectorAll('.t-card[data-app]').length");
  await js("document.querySelector('.t-card[data-app] .x-rm').click(); return 1");
  await until("document.querySelector('#ask[open]')", 2000);
  await js("document.querySelector('#ask-ok').click(); return 1");
  check("phone: × on a tracker card deletes it after asking", await until(`document.querySelectorAll('.t-card[data-app]').length === ${c0 - 1}`, 4000));
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || err);
} finally {
  console.log(JSON.stringify({ checks }));
  close();
}

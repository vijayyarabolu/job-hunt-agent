// GOAL15 §4 checks in headless Chrome (tests/test_goal15_resume_ui.py): a tracker row's Resume cell: × (hidden until
// hover on a desktop) -> "Upload resume" -> upload -> Replace ▾ -> a ready resumegen version -> Undo; the phone card.
// usage: node goal15_resume.mjs <base url> <token> <chrome path> <profile dir> <row id> <first file> <approved file>
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, ROW, FIRST, APPROVED] = process.argv.slice(2);
const { send, js, until, errors, checks, check, close } = await start(CHROME, PROFILE_DIR);
const S = JSON.stringify;
const ROW_EL = `document.querySelector('#view [data-app="${ROW}"]')`;
const CELL = `${ROW_EL}?.querySelector('td.c-resume')`;
const cellText = () => js(`return ${CELL}?.textContent || ''`);
const toasts = () => js("return [...document.querySelectorAll('.toast')].map((t) => t.textContent).join(' | ')");
const button = (label, where = CELL) => `[...(${where}?.querySelectorAll('button') || [])].find((b) => b.textContent === ${S(label)})`;
const load = async () => {
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:jane` });
  for (let i = 0; i < 30 && !(await js(`return document.body.dataset.view === 'table' && !!${ROW_EL}`)); i++) {
    await js("[...document.querySelectorAll('[data-tab=tracker]')].find((b) => b.offsetParent)?.click(); return true");
    await sleep(400);                                // start-up may route back to its first tab: click until it holds
  }
  await sleep(300);
};
// the page's file picker gets a PDF without a dialog (the test's own bytes)
const feedFiles = () => js(`const click = HTMLInputElement.prototype.click;
  HTMLInputElement.prototype.click = function () {
    if (this.type !== 'file') return click.call(this);
    const dt = new DataTransfer(); dt.items.add(new File(['%PDF-1.7\\n%%EOF\\n'], 'second.pdf', { type: 'application/pdf' }));
    this.files = dt.files; this.dispatchEvent(new Event('change'));
  }; return true`);

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await load();
  await feedFiles();
  check("the row's cell shows its resume with × and Replace ▾", (await cellText()).includes(FIRST)
    && await js(`return !!${button("×")} && !!${button("Replace ▾")} && !${button("Upload resume")}`), await cellText());

  // ---- × hides until the row is hovered (desktop) ----
  await js(`${ROW_EL}.scrollIntoView({ block: 'center' }); return true`);
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: 5, y: 5 });
  await sleep(200);
  const idle = await js(`return getComputedStyle(${button("×")}).opacity`);
  const r = await js(`const b = ${CELL}.getBoundingClientRect(); return { x: b.left + 20, y: b.top + b.height / 2 }`);
  await send("Input.dispatchMouseEvent", { type: "mouseMoved", x: r.x, y: r.y });
  await sleep(200);
  const hovered = await js(`return getComputedStyle(${button("×")}).opacity`);
  check("× shows on hover only (desktop)", idle === "0" && hovered === "1", `idle ${idle}, hovered ${hovered}`);

  // ---- × -> Upload resume -> upload ----
  await js(`${button("×")}.click(); return true`);
  const cleared = await until(`${button("Upload resume")} && !${button("×")} && !${CELL}.textContent.includes(${S(FIRST)})`);
  check("× removes the resume and Upload resume shows again", cleared && (await toasts()).includes("Resume removed"), `${await cellText()} | ${await toasts()}`);
  await js(`${button("Upload resume")}.click(); return true`);
  const uploaded = await until(`${CELL}.textContent.includes('_v2.pdf') && ${button("×")}`, 10000);
  check("the uploaded file becomes the row's resume", uploaded, await cellText());

  // ---- Replace ▾ -> a ready resumegen version ----
  await js(`${button("Replace ▾")}.click(); return true`);
  const listed = await until("[...document.querySelectorAll('#pop button.opt')].some((b) => b.textContent.includes('#7 · ready'))", 8000);
  const items = await js("return [...document.querySelectorAll('#pop button.opt')].map((b) => b.textContent)");
  check("Replace ▾ offers upload, the ready version and Generate again", listed && items[0].startsWith("Upload a file")
    && items.at(-1) === "Generate again", S(items));
  await js("[...document.querySelectorAll('#pop button.opt')].find((b) => b.textContent.includes('#7')).click(); return true");
  const linked = await until(`${CELL}.textContent.includes(${S(APPROVED)}) && !${CELL}.textContent.includes('_v2.pdf')`, 10000);
  check("the picked version is linked in place of the old one", linked && (await toasts()).includes(`Linked ${APPROVED}`), `${await cellText()} | ${await toasts()}`);

  // ---- Undo ----
  await js("[...document.querySelectorAll('.toast')].reverse().find((t) => t.textContent.includes('Linked'))?.querySelector('.undo').click(); return true");
  const undone = await until(`${CELL}.textContent.includes('_v2.pdf')`, 10000);
  check("Undo restores the row's previous resume", undone && (await toasts()).includes("Undone"), `${await cellText()} | ${await toasts()}`);

  // ---- the phone card: the resume button says what it does (the card's own × removes the row) ----
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 3, mobile: true });
  await load();
  const phone = await js(`const b = ${button("Remove resume", ROW_EL)}; return b ? getComputedStyle(b).opacity + ' ' + (b.offsetParent !== null) : 'none'`);
  check("on a phone Remove resume is always visible", phone === "1 true", phone);
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
}
close();
console.log(JSON.stringify({ checks }));

// GOAL23 §4/§6/§7 UI checks in headless Chrome: the Answers page (Needs you on top with option buttons, the bank
// editable, Addresses), its side-menu badge, Settings › Autofill extension (download + token), the phone layout.
// usage: node goal23_answers.mjs <base url> <token> <chrome path> <profile dir>  -> prints {"checks": [...]}
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR] = process.argv.slice(2);
const { send, js, until, errors, checks, check, click, clickText, server, close } = await start(CHROME, PROFILE_DIR);
const badge = () => js("return document.querySelector('#tabs [data-tab=answers] .n').textContent");

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}` });
  await until("document.querySelector('#tabs [data-tab=answers]')");
  check("the side menu has Answers with the Needs-you count", await until("document.querySelector('#tabs [data-tab=answers] .n').textContent === '3'", 8000), await badge());
  await click("#tabs [data-tab=answers]");
  check("Answers opens with Needs you on top", await until("document.querySelector('.af .af-sec h2')?.textContent === 'Needs you (3)'"),
    await js("return document.querySelector('.af .af-sec h2')?.textContent"));
  check("a choice question shows its options as buttons", await js("return [...document.querySelectorAll('.af-need')].some((c) => c.textContent.includes('worked for') && [...c.querySelectorAll('.af-opts .btn')].map((b) => b.textContent).join('|') === 'Yes|No')"));
  check("a legal question is marked your answer only", await js("return [...document.querySelectorAll('.af-need')].some((c) => c.textContent.includes('veteran') && c.querySelector('.tag.warn'))"));
  await js("[...document.querySelectorAll('.af-need')].find((c) => c.textContent.includes('worked for')).querySelector('.af-opts .btn:last-child').click()");
  check("answering takes it off Needs you", await until("document.querySelector('.af .af-sec h2')?.textContent === 'Needs you (2)'"));
  check("the badge counts down", await until("document.querySelector('#tabs [data-tab=answers] .n').textContent === '2'"), await badge());
  const bank = await server("/api/autofill/answers?profile=alex");
  check("the answer is saved in the bank", bank.bank.some((r) => r.key === "prev_worked" && r.answer === "No") || bank.bank.some((r) => r.answer === "No" && /worked/i.test(r.label)),
    JSON.stringify(bank.bank.filter((r) => /worked/i.test(r.label))));
  const text = "[...document.querySelectorAll('.af-need')].find((c) => c.textContent.includes('Why do you want'))";
  await js(`const i = ${text}.querySelector('input'); i.value = 'Their evaluation work.'; i.dispatchEvent(new Event('input')); ${text}.querySelector('.btn.primary').click()`);
  check("a typed answer saves too", await until("document.querySelector('.af .af-sec h2')?.textContent === 'Needs you (1)'"));
  check("Addresses shows the home line and the saved city", await js("const s = [...document.querySelectorAll('.af-sec')].find((x) => x.querySelector('h2')?.textContent === 'Addresses'); return s && s.textContent.includes('7480 Birdwood Ave, McLean, VA 22102') && s.textContent.includes('Austin')"),
    await js("return [...document.querySelectorAll('.af-sec')].find((x) => x.querySelector('h2')?.textContent === 'Addresses')?.textContent"));
  await js("const r = [...document.querySelectorAll('.af-addr')].find((x) => x.textContent.includes('McLean')); const [st] = r.querySelectorAll('input'); st.value = '7480 Birdwood Avenue Apt 12'; r.querySelector('.btn').click()");
  check("an edited address is saved street only, USPS style", await until("[...document.querySelectorAll('.af-addr .af-line')].some((x) => x.textContent === '7480 Birdwood Ave, McLean, VA 22102')"),
    await js("return [...document.querySelectorAll('.af-addr .af-line')].map((x) => x.textContent).join(' / ')"));
  // Remove (again after 3 s if a redraw from the edit's save swallowed the click): two round trips under load
  const removeMcLean = "(() => { const r = [...document.querySelectorAll('.af-addr')].find((x) => x.textContent.includes('McLean')); if (!r) return true; " +
    "if (!window.__rmAt || Date.now() - window.__rmAt > 3000) { window.__rmAt = Date.now(); r.querySelectorAll('.btn')[1].click(); } return false; })()";
  check("an address can be removed", await until(removeMcLean, 15000));
  const row = "[...document.querySelectorAll('.af-row')].find((r) => r.textContent.startsWith('Salary expectation'))";
  check("the whole bank is editable", await js(`return !!${row}?.querySelector('input, textarea')`), await js("return [...document.querySelectorAll('.af-row > span:first-child')].map((x) => x.textContent).slice(0, 40).join('|')"));
  await js(`const i = ${row}.querySelector('input, textarea'); i.value = '$140,000'; i.dispatchEvent(new Event('change'))`);
  check("a bank edit saves on its own", await until(`${row}.querySelector('.saved-state').textContent === 'Saved ✓'`));
  const after = await server("/api/autofill/answers?profile=alex");
  check("the server has the edit", after.bank.some((r) => r.label.startsWith("Salary expectation") && r.answer === "$140,000"));
  check("work history is listed", await js("return [...document.querySelectorAll('.af-sec h2')].some((h) => h.textContent === 'Work history') && document.querySelectorAll('.af-hrow').length >= 1"));

  // ---- Settings › Autofill extension ----
  await click("#settings-btn");
  await until("document.querySelector('.set-nav [data-sec=autofill]')");
  await click(".set-nav [data-sec=autofill]");
  check("Settings has Get the Autofill extension", await until("[...document.querySelectorAll('.set-body a.btn')].some((a) => a.textContent === 'Get the Autofill extension')"));
  check("it shows the 4 install steps", await js("return document.querySelectorAll('.set-body ol li').length === 4"));
  check("the token can be copied", await until("[...document.querySelectorAll('.set-body button')].some((b) => b.textContent === 'Copy token')"));
  const zip = await js("const a = [...document.querySelectorAll('.set-body a.btn')].find((x) => x.textContent === 'Get the Autofill extension'); const r = await fetch(a.href); const b = new Uint8Array(await r.arrayBuffer()); return [r.status, r.headers.get('content-type'), b[0], b[1], b.length]");
  check("the download is the extension zip", zip && zip[0] === 200 && zip[1] === "application/zip" && zip[2] === 0x50 && zip[3] === 0x4b, zip);

  // ---- phone ----
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await js("history.back()");
  await sleep(400);
  await js("document.querySelector('#btabs [data-tab=more]').click()");
  check("the phone's More menu has Answers", await until("[...document.querySelectorAll('#pop .opt')].some((o) => o.textContent.startsWith('Answers'))"));
  await clickText("#pop .opt", "Answers");
  check("Answers opens on a phone", await until("document.querySelector('.af .af-need')"));
  check("nothing scrolls sideways on a phone", await js("return document.documentElement.scrollWidth <= innerWidth + 1"),
    await js("return document.documentElement.scrollWidth"));
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || err);
} finally {
  close();
  console.log(JSON.stringify({ checks }));
}

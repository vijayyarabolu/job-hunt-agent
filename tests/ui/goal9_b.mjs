// GOAL9 area B: the job list rows, in headless Chrome against a throwaway DB and a fake resumegen (tests/test_goal9_b.py).
// The row overlap check (rowcheck.mjs) at 1440 / 1280 / 1024 / 390 px over every tag and chip combination, hover /
// selected / picking / pick-bar states; the hover labels (§6, tips.mjs); the row shortcuts; optimistic updates and
// rollback; the "N new jobs" pill. Screenshots go to <evidence dir>.
// usage: node goal9_b.mjs <base url> <token> <chrome path> <profile dir> <fake resumegen url> <evidence dir> <marker file>
import { writeFileSync } from "node:fs";
import { sleep, start } from "./cdp.mjs";
import { ROW_CHECK } from "./rowcheck.mjs";
import { hoverTip, missingTips } from "./tips.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR, FAKE, OUT, MARKER] = process.argv.slice(2);
const { send, js, until, key, errors, checks, check, clickText, server, state, close } = await start(CHROME, PROFILE_DIR);
const P = "tester";
const WIDTHS = [1440, 1280, 1024];
const INSTANT_MS = 150;
const fake = async (path, body) => (await fetch(`${FAKE}${path}`, body ? { method: "POST", body: JSON.stringify(body) } : {})).json();
let loads = 0;
const load = async (hash) => {
  await send("Page.navigate", { url: `${BASE}/?n=${++loads}#${hash}` });
  await sleep(300);
  await until("document.querySelector('#list-head h1')?.textContent && !document.querySelector('#list[aria-busy]')", 15000);
  await until("document.body.classList.contains('rg-on') && document.querySelector('#list .rgc:not(:empty)')", 9000);
  await until("document.getElementById('tip')", 4000);
};
const metrics = (w) => send("Emulation.setDeviceMetricsOverride", { width: w, height: w === 390 ? 844 : 900, deviceScaleFactor: w === 390 ? 2 : 1, mobile: w === 390 });
const shot = async (name) => { const r = await send("Page.captureScreenshot", { format: "png" }); writeFileSync(`${OUT}/${name}.png`, Buffer.from(r.data, "base64")); };
const centre = (sel) => js(`const r = document.querySelector(${JSON.stringify(sel)})?.getBoundingClientRect(); return r ? [r.left + r.width / 2, r.top + r.height / 2] : null`);
const mouse = async (type, sel, extra = {}) => { const c = await centre(sel); if (!c) return false; await send("Input.dispatchMouseEvent", { type, x: c[0], y: c[1], button: "left", clickCount: 1, ...extra }); return true; };
const realClick = async (sel, modifiers = 0) => { await mouse("mouseMoved", sel); await mouse("mousePressed", sel, { modifiers }); await mouse("mouseReleased", sel, { modifiers }); await sleep(120); };
// resumegen GOAL3 §7: every open starts from the default filters, so More -> Show too senior is ticked after each load
const showSenior = async () => {
  await js("[...document.querySelectorAll('#fchips .chip')].find((c) => c.textContent.startsWith('More')).click()");
  await until("[...document.querySelectorAll('#pop .opt')].some((o) => o.textContent.includes('Show too senior'))", 4000);
  await js("[...document.querySelectorAll('#pop .opt')].find((o) => o.textContent.includes('Show too senior')).click()");
  await js(press("Escape"));
  await until("[...document.querySelectorAll('#fchips .chip.on')].some((c) => c.textContent.includes('too senior')) && !document.querySelector('#list[aria-busy]')", 8000);
  await sleep(300);
  await until("document.querySelector('#list .rgc:not(:empty)')", 9000);
};
// GOAL9 (Lighthouse CLS 0.34 on the phone feed): rows must not move when the lazy resume layer (rg.js + rg.css) arrives
const SHIFTS = `window.__shifts = []; new PerformanceObserver((l) => { for (const e of l.getEntries()) for (const s of e.sources)
  if (s.node?.closest?.("#list, #list-head")) window.__shifts.push((s.node.id || s.node.className) + " " + Math.round(s.previousRect.y) + "->" + Math.round(s.currentRect.y)
    + " h" + Math.round(s.previousRect.height) + "->" + Math.round(s.currentRect.height)); }).observe({ type: "layout-shift", buffered: true });`;
const noShift = async (tag) => { const moved = await js("return window.__shifts || ['no recorder']"); check(`rows keep their place while the page loads: ${tag}`, moved.length === 0, moved.slice(0, 6).join(" | ")); };
// the row check can come back empty while a busy machine is still painting (seen with another Chrome test running):
// ask again a few times before calling it, and report a missing answer as such instead of throwing
const rowIssues = async () => { for (let i = 0; i < 5; i++) { const got = await js("return window.__rowCheck?.()"); if (Array.isArray(got)) return got; await sleep(300); } return ["the row check gave no answer"]; };
const overlap = async (tag) => { const issues = await rowIssues(); check(`no overlap: ${tag}`, issues.length === 0, issues.slice(0, 8).join(" | ")); };
const labels = async (tag) => { const miss = await missingTips(js, "#pane-list"); check(`labels: ${tag}`, miss.length === 0, miss.join(" | ")); };
const rowOf = (uid) => js(`return document.querySelector('#list .rgc[data-uid=${JSON.stringify(uid)}]')?.closest('.row')?.id || ''`);
const text = (sel) => js(`return document.querySelector(${JSON.stringify(sel)})?.textContent || ''`);
// time from a synthetic keydown / click until the DOM shows the change (rAF polling, so ~1 frame of slack)
const measure = (act, cond) => js(`return await new Promise((res) => { const t0 = performance.now(); ${act};
  const tick = () => { if (${cond}) res(Math.round(performance.now() - t0)); else if (performance.now() - t0 > 3000) res(-1); else requestAnimationFrame(tick); }; tick(); })`);
const press = (k) => `document.dispatchEvent(new KeyboardEvent("keydown", { key: ${JSON.stringify(k)}, bubbles: true }))`;
const timings = {};

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await send("Fetch.enable", { patterns: [{ urlPattern: `${BASE}/api/*`, requestStage: "Request" }] });
  await send("Page.addScriptToEvaluateOnNewDocument", { source: SHIFTS });
  await metrics(1440);
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}#recommended:${P}` });
  await until("document.querySelector('#list-head h1')?.textContent", 15000);
  const all = (await server(`/api/jobs?profile=${P}&tab=recommended&senior=1`)).jobs;
  const uids = [0, 1, 2, 3, 4, 5, 6].map((i) => all.find((j) => j.uid.endsWith(`:${i}`))?.uid || `missing:${i}`);   // by seed index
  check("the seeded jobs are in the feed", uids.slice(1).every((u) => !u.startsWith("missing")), JSON.stringify(all.map((j) => [j.uid, j.state, j.labels.too_senior])));
  // chips in every status, before the page opens (its first poll paints them): job 1 queued, 2 approved, 3 needs you,
  // 4 generating, 5 ready; 6 (applied) has no resume -> the Generate slot; 0 is closed (job 1 says "Applied before")
  const add = (i, status, extra = {}) => fake("/__add", { profile: P, company: "x", role: "y", jd_text: "z".repeat(100), source_job_id: uids[i], status, ...extra });
  await add(1, "queued");
  await add(2, "ready", { drive_file_id: "F2", drive_link: "https://drive.example/F2" });
  await add(3, "failed", { error: "The base resume has no dates" });
  await add(4, "generating");
  await add(5, "ready");
  await js("window.open = (u) => { window.__opened = u; return null; }");

  // ---- §2 layout at 1440 / 1280 / 1024 ----
  for (const w of WIDTHS) {
    await metrics(w);
    await load(`recommended:${P}`);
    await noShift(String(w));
    await showSenior();
    await js("window.open = (u) => { window.__opened = u; return null; }");
    await js(ROW_CHECK);
    if (w === 1440) {
      const tags = await js("return [...document.querySelectorAll('#list .tag')].map((t) => t.textContent)");
      const want = ["Applied", "Applied before", "Saved", "Too senior", "years not stated", "Asks 3 yrs"];
      check("every tag and chip combination is on the page", want.every((t) => tags.includes(t)) && await js("return !!document.querySelector('#list .liked')"),
        await js("return [...document.querySelectorAll('#list .row')].map((r) => r.querySelector('.rgc').dataset.uid + ' ' + [...r.querySelectorAll('.tag')].map((t) => t.textContent).join('/') + ' ' + r.querySelector('.rgc').textContent).join(' | ')"));
      const chips = await js("return [...document.querySelectorAll('#list .rgc')].map((c) => c.className + '=' + c.textContent)");
      const wantChips = ["rg-queued=Resume queued", "rg-ready rg-ok=Resume approved", "rg-needs-you=Resume needs you", "rg-generating=Generating resume", "rg-ready=Resume ready", "rg-gen=Generate resume"];
      check("the chips show every status", wantChips.every((c) => chips.some((x) => x.endsWith(c))), chips.join(", "));
      const slot = await js(`const b = document.querySelector('#list .rg-gen'); const row = b.closest('.row'); const r = b.getBoundingClientRect();
        const side = [...row.querySelectorAll('.row-side time, .row-side .worth, .ring')].map((x) => x.getBoundingClientRect());
        return { inLine: !!b.closest('.row-meta'), pos: getComputedStyle(b).position, clear: side.every((s) => r.right <= s.left || r.left >= s.right || r.bottom <= s.top || r.top >= s.bottom) }`);
      check("the Generate slot sits in the chip line, not over the time / worth / score", slot.inLine && slot.pos !== "absolute" && slot.clear, JSON.stringify(slot));
      check("the chip replaces the Generate slot once a resume exists", await js(`const r = document.querySelector('#list .rgc.rg-ready').closest('.row'); return r.querySelectorAll('.rgc').length === 1 && !r.querySelector('.rg-gen')`));
    }
    await overlap(`${w} plain`);
    await labels(`${w} plain`);
    await shot(`rows-${w}`);
    await mouse("mouseMoved", "#list .rg-gen");
    await sleep(100);
    await overlap(`${w} hovered Generate row`);
    await realClick(`#${await rowOf(uids[2])} .row-title`);
    await overlap(`${w} long row selected`);
    await shot(`rows-${w}-selected`);
    await js("document.querySelector('#list').focus()");
    await key(" ");                                       // pick the selected row: the box + the pick bar
    await until(".pickbar:not([hidden])");
    await overlap(`${w} picking with the bar`);
    await labels(`${w} picking with the bar`);
    await shot(`rows-${w}-picking`);
    await clickText(".pickbar .btn", "Clear");
  }

  // ---- §6 tips at 1440 ----
  const tip = async (name, sel, want) => { const t = await hoverTip(send, js, sleep, sel); check(name, t.found && t.text.startsWith(want) && !t.overlaps, JSON.stringify(t)); };
  const saved = await rowOf(uids[5]), senior = await rowOf(uids[3]), free = await rowOf(uids[6]);
  check("the seeded rows are on the page", saved && senior && free, `${saved} ${senior} ${free}`);
  await tip("the pick box tip", `#${saved} .pick`, "Select for Generate for selected (Space");
  await tip("a tag tip", `#${saved} .tag.soft:not(.yrs)`, "Saved: in your tracker (s");
  await tip("a resume chip tip", "#list .rgc.rg-needs-you", "Resume needs you: open it · The base resume");
  await tip("the Generate slot tip", "#list .rg-gen", "Generate a tailored resume (g)");
  await mouse("mouseMoved", "#lh-title");
  await js("document.querySelector('.sel-mode').focus()");
  check("a keyboard-focus tip", await until("!document.getElementById('tip').hidden && document.getElementById('tip').textContent.startsWith('Select jobs')", 2000), await text("#tip"));
  await js("document.querySelector('#list').focus()");

  // ---- selection ----
  const n3 = Number(senior.slice(4));
  timings.click = await measure(`document.querySelector('#${senior} .row-title').click()`, `document.querySelector('#${senior}').getAttribute('aria-selected') === 'true' && document.querySelector('#detail .d-title')?.textContent === 'Senior AI Engineer'`);
  check("a click selects the row at once", timings.click >= 0 && timings.click < INSTANT_MS, `${timings.click} ms`);
  const bar = await js(`const r = document.querySelector('#${senior}'); const b = document.querySelector('#sel-bar'); return { t: b.style.transform, h: b.style.height, top: r.offsetTop, hh: r.offsetHeight, hidden: b.hidden }`);
  check("the selection bar follows the row", !bar.hidden && bar.t === `translateY(${bar.top}px)` && bar.h === `${bar.hh}px`, JSON.stringify(bar));
  await key("j");
  const afterJ = await js("return document.querySelector('#list [aria-selected=true]').id");
  await key("k");
  const afterK = await js("return document.querySelector('#list [aria-selected=true]').id");
  check("j / k move the selection", afterJ === `job-${n3 + 1}` && afterK === senior, `${afterJ} ${afterK}`);
  check("focus stays on the list", (await js("return document.activeElement?.id")) === "list");
  await key("o");
  check("o opens the posting", (await js("return window.__opened || ''")).endsWith("/acme/3"), await js("return window.__opened"));
  await key(" ");
  check("Space picks", (await js(`return document.querySelector('#${senior}').getAttribute('aria-checked')`)) === "true");
  await realClick(`#job-${n3 + 2} .row-title`, 8);                                    // Shift
  const picked = await js("return [...document.querySelectorAll('#list .row[aria-checked=true]')].map((r) => r.id).join()");
  check("Shift-click picks a range without selecting text", picked === `${senior},job-${n3 + 1},job-${n3 + 2}` && (await js("return getSelection().toString()")) === "", `${picked} / '${await js("return getSelection().toString()")}'`);
  check("the bar counts them", (await text(".pickbar .pb-n")) === "3 selected");
  await js("[...document.querySelectorAll('.pickbar .btn')].find((b) => b.textContent === 'Clear').focus()");
  await clickText(".pickbar .btn", "Clear");
  check("Clear keeps the focus on the list", await js("return document.querySelector('.pickbar').hidden && document.activeElement?.id === 'list'"), await js("return document.activeElement?.tagName"));

  // ---- Generate from the row slot: one click, one job; the chip takes the slot ----
  let st = await fake("/__state");
  const before = st.posts.length;
  state.failNext = "/api/resumes/generate";                     // refused: the chip shows at once, then goes back
  timings.generateRefused = await measure(`document.querySelector('#${free} .rg-gen').click()`, `document.querySelector('#${free} .rgc')?.textContent === 'Resume queued'`);
  check("a refused Generate rolls back the chip", timings.generateRefused >= 0 && timings.generateRefused < INSTANT_MS
    && await until(`document.querySelector('#${free} .rg-gen') && [...document.querySelectorAll('.toast.err p')].some((p) => p.textContent.includes('server said no'))`),
    `${timings.generateRefused} ms, then ${await text(`#${free} .rgc`)}`);
  await js("document.querySelectorAll('.toast').forEach((t) => t.remove())");
  await mouse("mouseMoved", `#${free} .rg-gen`);
  timings.generate = await measure(`document.querySelector('#${free} .rg-gen').click()`, `document.querySelector('#${free} .rgc')?.textContent === 'Resume queued'`);
  check("the row Generate shows queued at once", timings.generate >= 0 && timings.generate < INSTANT_MS, `${timings.generate} ms`);
  check("the row Generate queues that one job", await until(`document.querySelector('#${free} .rgc')?.textContent === 'Resume queued' && !document.querySelector('#${free} .rg-gen')`), await text(`#${free} .rgc`));
  for (let t = 0; t < 40 && (await fake("/__state")).posts.length === before; t++) await sleep(100);   // the chip shows before the POST lands
  st = await fake("/__state");
  check("in one request of one job", st.posts.length === before + 1 && st.posts[before].length === 1 && st.posts[before][0].source_job_id === uids[6], JSON.stringify(st.posts.map((p) => p.length)));

  // ---- s / l / x: at once in the row and the detail pane; rollback when the server refuses ----
  const r4 = await rowOf(uids[4]);
  await js(`document.querySelector('#${r4} .row-title').click()`);
  await until("document.querySelector('#detail .d-title')?.textContent === 'AI Engineer'");
  timings.save = await measure(press("s"), `document.querySelector('#${r4} .tag')?.textContent === 'Saved'`);
  check("s saves the row at once", timings.save >= 0 && timings.save < INSTANT_MS, `${timings.save} ms`);
  check("the detail pane agrees", await until("document.querySelector('#detail [aria-label^=\"Save for later\"]')?.getAttribute('aria-pressed') === 'true'"));
  await sleep(300);
  await key("s");
  await until(`document.querySelector('#${r4} .tag')?.textContent !== 'Saved'`);
  state.failNext = "/api/triage";
  await key("s");
  check("a refused save rolls back with a toast", await until(`document.querySelector('#${r4} .tag')?.textContent !== 'Saved' && [...document.querySelectorAll('.toast.err p')].some((p) => p.textContent.startsWith('Not saved'))`), await text(`#${r4} .row-meta`));
  timings.like = await measure(press("l"), `document.querySelector('#${r4} .liked')`);
  check("l likes at once", timings.like >= 0 && timings.like < INSTANT_MS && await until("document.querySelector('#detail [aria-label^=\"Like\"]')?.getAttribute('aria-pressed') === 'true'"), `${timings.like} ms`);
  await sleep(300);
  await key("l");
  await until(`!document.querySelector('#${r4} .liked')`);
  await js("document.querySelectorAll('.toast').forEach((t) => t.remove())");
  const rowsBefore = await js("return document.querySelectorAll('#list .row[data-i]').length");
  const headBefore = await text("#lh-title .muted");
  await key("x");
  check("x drops the row and the count", await until(`document.querySelectorAll('#list .row[data-i]').length === ${rowsBefore - 1} && document.querySelector('#lh-title .muted').textContent !== ${JSON.stringify(headBefore)}`), `${headBefore} -> ${await text("#lh-title .muted")}`);
  await clickText(".toast .undo", "Undo");
  check("Undo puts it back", await until(`document.querySelectorAll('#list .row[data-i]').length === ${rowsBefore} && document.querySelector('#lh-title .muted').textContent === ${JSON.stringify(headBefore)}`), `${await js("return document.querySelectorAll('#list .row[data-i]').length")} rows, ${await text("#lh-title .muted")}`);
  await sleep(300);
  await js("document.querySelectorAll('.toast').forEach((t) => t.remove())");
  state.failNext = "/api/triage";
  await key("x");
  await until("[...document.querySelectorAll('.toast.err p')].some((p) => p.textContent.startsWith('Not saved'))", 4000);
  await sleep(300);                                                       // past the 160 ms leave animation
  const after = await js("return { rows: document.querySelectorAll('#list .row[data-i]').length, head: document.querySelector('#lh-title .muted').textContent, dup: new Set([...document.querySelectorAll('#list .rgc[data-uid]')].map((c) => c.dataset.uid)).size }");
  check("a refused dismiss puts it back", after.rows === rowsBefore && after.head === headBefore && after.dup === rowsBefore, JSON.stringify(after));
  await key("m");
  check("m opens the Mark applied dialog", await until("document.querySelector('dialog[open]')"), await js("return [...document.querySelectorAll('.toast p')].map((p) => p.textContent).join(' | ') + ' / active ' + document.activeElement?.tagName + '#' + document.activeElement?.id + ' / ' + [...document.querySelectorAll('dialog')].map((d) => d.id + ':' + d.open).join(',')"));
  await key("Escape");
  await until("!document.querySelector('dialog[open]')");
  await key("b");
  check("b shows the match breakdown", await until("document.querySelector('#breakdown')"));

  // ---- "N new jobs", then g on the fresh row ----
  writeFileSync(MARKER, "go");
  check("the new-jobs pill appears", await until("document.querySelector('.pill-new')", 40000), await text("#list-head"));
  const n = await js("return document.querySelectorAll('#list .row[data-i]').length");
  const total = await text("#lh-title .muted");
  await js("document.querySelector('.pill-new').click()");
  check("the pill inserts the fresh row", await until(`document.querySelectorAll('#list .row[data-i]').length === ${n + 1} && document.querySelector('#list .row.enter') && document.querySelector('#lh-title .muted').textContent !== ${JSON.stringify(total)}`),
    `${n} -> ${await js("return document.querySelectorAll('#list .row[data-i]').length")} rows, head ${await text("#lh-title .muted")}`);
  await js(ROW_CHECK);
  await overlap("1440 with the fresh row");
  await js("document.querySelector('#list .row.enter .row-title').click()");
  await until("document.querySelector('#list .row.enter .rg-gen')");
  timings.g = await measure(press("g"), "document.querySelector('#list .row.enter .rgc')?.textContent === 'Resume queued'");
  check("g queues the selected job", timings.g >= 0, `${timings.g} ms (after the server answered)`);
  const ages = await js("return [...document.querySelectorAll('#list time[data-ts]')].map((t) => t.textContent)");
  check("ages read as minutes / hours / days", ages.length > 0 && ages.every((a) => /^~?(\d+ min|\d+ h \d+ min|\d+ d( \d+ h)?)$/.test(a)), ages.join(", "));

  check("no page errors on the desktop", errors.length === 0, errors.join(" | ") + " @ " + await js("return location.href"));

  // ---- the phone ----
  await metrics(390);
  await send("Emulation.setTouchEmulationEnabled", { enabled: true });
  await load(`recommended:${P}`);
  await noShift("390");
  await showSenior();
  await js(ROW_CHECK);
  await overlap("390 plain");
  await labels("390 plain");
  await shot("rows-390");
  await clickText(".sel-mode", "Select");
  await until("document.body.classList.contains('pick-mode')");
  await overlap("390 picking");
  const box = await js("const r = document.querySelector('#job-0 .pick').getBoundingClientRect(); const g = document.querySelector('#job-0 .ring').getBoundingClientRect(); return { w: r.width, clear: r.right <= g.left }");
  check("the phone pick box is 22 px and clear of the ring", box.w >= 22 && box.clear, JSON.stringify(box));
  const tap = await centre("#job-0 .pick");
  await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: tap[0], y: tap[1] }] });
  await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await until(".pickbar:not([hidden])");
  check("a tap on the box picks the row", (await js("return document.querySelector('#job-0').getAttribute('aria-checked')")) === "true");
  await overlap("390 picked with the bar");
  const pb = await js("const b = document.querySelector('.pickbar:not([hidden])'); if (!b) return null; const r = b.getBoundingClientRect(); return { l: r.left, r: r.right, h: Math.round(r.height), w: innerWidth }");
  check("the phone pick bar spans the screen in two lines at most", pb && pb.l >= 0 && pb.r <= pb.w && pb.h <= 130 && pb.r - pb.l >= pb.w - 30, JSON.stringify(pb));
  await labels("390 picked with the bar");
  await shot("rows-390-picking");
  const clear = await js(`const w = document.querySelector('#list-wrap'); w.scrollTop = w.scrollHeight; await new Promise(requestAnimationFrame);
    const rows = document.querySelectorAll('#list .row[data-i]'); const last = rows[rows.length - 1].getBoundingClientRect();
    const bar = document.querySelector('.pickbar').getBoundingClientRect(); const v = { last: Math.round(last.bottom), bar: Math.round(bar.top) };
    w.scrollTop = 0; return v`);
  check("the last row scrolls clear of the pick bar", clear.last <= clear.bar, JSON.stringify(clear));
  const target =await js("const c = document.querySelector('#list .rgc:not(:empty)'); const a = getComputedStyle(c, '::after'); return { h: c.getBoundingClientRect().height, top: a.top, bottom: a.bottom }");
  check("the phone resume chip / Generate slot is a 44 px target", target.h + 2 * Math.abs(parseFloat(target.top)) >= 44, JSON.stringify(target));
  await clickText(".pickbar .btn", "Clear");                               // the floating bar must not cover the row
  await until("document.querySelector('.pickbar').hidden");
  await sleep(400);                                                       // the tap's synthesized mouse events are done
  await key("Escape");                                                    // and any tip they armed is gone
  await js("document.querySelector('#list .rgc.rg-needs-you').scrollIntoView({ block: 'center' }); return 1");   // GOAL35: the feed's search box sits above the list
  await sleep(200);
  const t = await centre("#list .rgc.rg-needs-you");                      // a control: Chrome snaps a touch to the nearest clickable element
  await send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: t[0], y: t[1] }] });
  await sleep(650);
  check("a phone long-press tip shows", await js("const x = document.getElementById('tip'); return !x.hidden && x.textContent.startsWith('Resume needs you: open it')"), await text("#tip") + " / under " + await js(`const e = document.elementFromPoint(${t[0]}, ${t[1]}); return e.tagName + "." + e.className`));
  await send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await sleep(100);
  check("release hides it", await js("return document.getElementById('tip').hidden"));
  check("screenshots saved", true, OUT);
  check("timings (ms)", true, JSON.stringify(timings));
  check("no page errors", errors.length === 0, errors.join(" | ") + " @ " + await js("return location.href + ' iframes ' + [...document.querySelectorAll('iframe')].map((f) => f.src).join()"));
} catch (err) {
  check("runner", false, err.stack || err);
} finally {
  console.log(JSON.stringify({ checks }));
  close();
}

// GOAL21 §2 / §4 in headless Chrome (tests/test_goal21_ui.py): the grouped side menu with counts and one active item,
// the person switch on screen (one click), the status button and its popover, Settings as a page with sections (gear,
// "," and the menus; Back / Esc), the links to resumegen, and the phone: five sections in a bottom bar, each section's
// views as chips, no sideways scroll. Both people, 1440 and 390 px.
// usage: node goal21_ui.mjs <base url> <token> <chrome path> <profile dir>
import { sleep, start } from "./cdp.mjs";

const [BASE, TOKEN, CHROME, PROFILE_DIR] = process.argv.slice(2);
const { send, js, until, key, errors, check, checks, close } = await start(CHROME, PROFILE_DIR);
const S = JSON.stringify;
let loads = 0;
const load = async (hash) => {
  await send("Page.navigate", { url: `${BASE}/?token=${TOKEN}&n=${++loads}#${hash}` });
  await until("document.querySelectorAll('#list .row[data-i]').length > 0 && !document.querySelector('#list[aria-busy]')", 10000);
  await sleep(300);
};
const size = (width, height, mobile = false) => send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: mobile ? 3 : 1, mobile });
const visible = (sel) => js(`return [...document.querySelectorAll(${S(sel)})].filter((e) => e.offsetParent !== null).map((e) => e.textContent.trim())`);
const noSideways = () => js("return document.scrollingElement.scrollWidth <= innerWidth && document.body.scrollWidth <= innerWidth");
const SET_OPEN = "document.body.dataset.view === 'settings' && document.querySelector('.set-nav')";

try {
  await send("Runtime.enable");
  await send("Page.enable");
  await size(1440, 900);
  await load("recommended:tester");

  // ---- the side menu: four groups, the views in order, counts, one active item ----
  const side = await js(`return { heads: [...document.querySelectorAll('#side h2')].map((x) => x.textContent),
    tabs: [...document.querySelectorAll('#tabs [data-tab]')].map((b) => b.dataset.tab),
    on: [...document.querySelectorAll('#tabs [aria-selected=true]')].map((b) => b.dataset.tab),
    labels: [...document.querySelectorAll('#tabs [data-tab] .lbl')].map((x) => x.textContent) }`);
  // an Autofill group with Answers at the end
  check("side menu: Jobs, Resumes, Applications, Insights, Autofill", S(side.heads) === S(["Jobs", "Resumes", "Applications", "Insights", "Autofill"]), S(side.heads));
  check("side menu: the views in their groups' order", S(side.tabs) === S(["recommended", "saved", "archived", "resumes", "tracker", "applied", "interviewing", "followups", "stats", "answers"]), S(side.tabs));
  check("side menu: exactly one active item", S(side.on) === S(["recommended"]), S(side.on));
  check("side menu: every item has a name and a hover label", await js("return [...document.querySelectorAll('#tabs [data-tab]')].every((b) => b.querySelector('.lbl').textContent.trim() && b.title)"));
  await js("document.querySelector('#tabs [data-tab=tracker]').click(); return true");
  check("side menu: a click opens the view and marks it", await until("location.hash === '#tracker' && document.querySelector('#tabs [data-tab=tracker]').getAttribute('aria-selected') === 'true' && document.querySelectorAll('#tabs [aria-selected=true]').length === 1", 6000));
  // W14: the app's Status is the column after the frozen Company / Role (it was ~20th); the Sheet keeps its order
  const heads = "return [...document.querySelectorAll('.g thead th[data-j] .g-hl')].map((x) => x.firstChild.textContent)";
  check("Tracker: Status comes right after the frozen columns", await until(`(${heads.slice(7)})[2] === 'Status'`, 8000), S(await js(heads)));
  await key("1");
  await until("location.hash === '#recommended'", 4000);

  // ---- the person switch: always on screen, one click ----
  const who = await js("return [...document.querySelectorAll('#who [data-person]')].map((b) => [b.dataset.person, b.getAttribute('aria-pressed'), b.textContent.trim()])");
  check("person switch: both people on screen, one pressed", who.length === 2 && who.filter((w) => w[1] === "true").length === 1 && who.every((w) => w[2]), S(who));
  await js("document.querySelector('#who [data-person=tester2]').click(); return true");
  check("person switch: one click switches", await until("document.querySelector('#who [data-person=tester2]').getAttribute('aria-pressed') === 'true' && !document.querySelector('#list[aria-busy]')", 6000));
  check("person switch: the resumegen link follows the person", (await js("return document.querySelector('#rg-link').getAttribute('href')")).includes("profile=tester2"),
    await js("return document.querySelector('#rg-link').getAttribute('href')"));
  await js("document.querySelector('#who [data-person=tester]').click(); return true");
  await until("document.querySelector('#who [data-person=tester]').getAttribute('aria-pressed') === 'true' && !document.querySelector('#list[aria-busy]')", 6000);

  // ---- the status button: one dot + a word, details in a popover ----
  const st = await js("const b = document.querySelector('#status'); return { text: b.querySelector('.status-text').textContent, dot: !!b.querySelector('.dot'), label: b.getAttribute('aria-label') }");
  check("status: a dot and a word, labelled", st.dot && /^(All good|Needs you|\d+ notices?)$/.test(st.text) && st.label.startsWith("Status"), S(st));
  await js("document.querySelector('#status').click(); return true");
  check("status: a click opens its popover", await until("!document.querySelector('#pop').hidden && document.querySelector('#pop .st-pop h3')?.textContent === 'Status'", 3000));
  await key("Escape");
  check("status: Esc closes it", await until("document.querySelector('#pop').hidden", 2000));

  // ---- area 3: the list head, Priority, labelled job actions, Generate resume as a button ----
  const head = await js("return document.querySelector('#list-head').textContent");
  check("list head: new jobs counted over the last 24 h", /\d+ new in the last 24 h/.test(head) && !head.includes("today ·"), head);
  check("rows say Priority, not worth", await js("return [...document.querySelectorAll('#list .row[data-i] .worth')].every((w) => w.textContent.startsWith('Priority ') || w.textContent === 'scoring')"));
  await js("document.querySelector('#list .row[data-i] .row-title').click(); return true");
  await until("document.querySelector('#detail .acts .lbl-btn')", 6000);
  const acts = await js("return [...document.querySelectorAll('#detail .acts .lbl-btn')].map((b) => [b.querySelector('.lbl-w')?.textContent, !!b.querySelector('svg'), b.getAttribute('aria-label') || ''])");
  check("job actions: an icon and a word each", S(acts.map((a) => a[0])) === S(["Like", "Save", "Not interested", "Contacts", "Remove", "More"]) && acts.every((a) => a[1] && a[2].toLowerCase().includes(a[0].toLowerCase())), S(acts));
  check("job detail: Priority with its explanation", await js("const w = document.querySelector('#detail .worth-line'); return !!w && w.textContent.startsWith('Priority') && (w.textContent.includes('still scoring') || document.querySelector('#detail .worth-line .info')?.title.startsWith('Priority, 0–100'))"),
    await js("return document.querySelector('#detail .worth-line')?.textContent"));
  const gen = await js("const g = document.querySelector('#list .rg-gen'); if (!g) return null; const st = getComputedStyle(g); return { r: parseFloat(st.borderTopLeftRadius), dashed: st.borderTopStyle === 'dashed', plus: getComputedStyle(g, '::before').content }");
  check("rows: Generate resume looks like a button, not a tag", !gen || (gen.r <= 8 && !gen.dashed && gen.plus.includes("+")), S(gen));
  await key("Escape");

  // ---- Settings: a page with sections; the gear, ",", Back and Esc ----
  await js("document.querySelector('#settings-btn').click(); return true");
  check("Settings: the gear opens the page", await until(SET_OPEN, 6000));
  const secs = await js("return [...document.querySelectorAll('.set-nav [data-sec]')].map((b) => b.textContent)");
  check("Settings: the seven sections", S(secs) === S(["Profile & answers", "Job filters", "Daily goal", "Accounts", "Resume generator", "Autofill extension", "About & help"]), S(secs));   // GOAL23 §6
  const bodies = {};
  for (const sec of ["answers", "filters", "goal", "accounts", "resumegen", "about"]) {
    await js(`document.querySelector('.set-nav [data-sec=${sec}]').click(); return true`);
    await until(`document.querySelector('.set-nav [data-sec=${sec}][aria-current=page]')`, 3000);
    await sleep(150);
    bodies[sec] = await js("return document.querySelector('.set-body')?.textContent.length || 0");
    if (sec === "resumegen") bodies.rgLink = await js("return [...document.querySelectorAll('.set-body a')].some((a) => a.getAttribute('href').startsWith('/open/resumegen'))");
  }
  const { rgLink, ...lengths } = bodies;
  check("Settings: every section shows its content", Object.values(lengths).every((n) => n > 40), S(lengths));
  check("Settings: the resume generator section links to resumegen", rgLink === true);
  check("Settings: the view title and no active menu item", await js("return document.querySelector('#view-title').textContent === 'Settings' && !document.querySelector('#tabs [aria-selected=true]')"));
  await js("document.querySelector('.set-back').click(); return true");
  check("Settings: Back returns to the list", await until("document.body.dataset.view === 'jobs' && location.hash === '#recommended'", 4000), await js("return location.hash"));
  await js("document.querySelector('#list').focus(); return true");
  await key(",");
  check("Settings: the , key opens it", await until(SET_OPEN, 4000));
  await key("Escape");
  check("Settings: Esc closes it", await until("document.body.dataset.view === 'jobs'", 4000));
  const base = await js("return getComputedStyle(document.body).fontSize");
  check("type: body text is at least 14 px", parseFloat(base) >= 14, base);

  // ---- the phone: five sections at the bottom, each section's views as chips, nothing scrolls sideways ----
  await size(390, 844, true);
  await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  await load("recommended:tester2");
  check("phone: the bottom bar has the five sections", S(await visible("#btabs [data-tab] > span:not(.n)")) === S(["Jobs", "Resumes", "Applications", "Stats", "More"]), S(await visible("#btabs [data-tab] > span:not(.n)")));
  check("phone: the side menu is not on screen", await js("return !document.querySelector('#side').offsetParent || document.querySelector('#side').getBoundingClientRect().width === 0"));
  check("phone: Jobs shows its three views as chips", S(await visible("#subtabs [data-sub]")) === S(["Recommended", "Saved", "Archived"]), S(await visible("#subtabs [data-sub]")));
  check("phone: the person switch is on screen", await js("return [...document.querySelectorAll('#who [data-person]')].filter((b) => b.offsetParent).length === 2"));
  check("phone: the list has no sideways scroll", await noSideways());
  await js("document.querySelector('#btabs [data-tab=tracker]').click(); return true");
  check("phone: Applications opens the Tracker with its four views", await until("location.hash === '#tracker' && [...document.querySelectorAll('#subtabs [data-sub]')].map((b) => b.textContent).join() === 'Tracker,Applied,Interviewing,Follow-ups'", 6000),
    S(await visible("#subtabs [data-sub]")));
  check("phone: the top bar names the view", await until("document.querySelector('#view-title').textContent === 'Tracker'", 2000), await js("return document.querySelector('#view-title').textContent"));
  await js("document.querySelector('#subtabs [data-sub=followups]').click(); return true");
  check("phone: a chip switches inside the section", await until("location.hash === '#followups' && document.querySelector('#btabs [data-tab=tracker]').getAttribute('aria-current') === 'page'", 6000));
  check("phone: the Tracker views have no sideways scroll", await noSideways());
  check("phone: a row of chips that scrolls sideways fades at the side with more (W15)",
    await until("document.querySelector('#subtabs').classList.contains('fade-r') || document.querySelector('#subtabs').scrollWidth <= document.querySelector('#subtabs').clientWidth", 4000),
    await js("const e = document.querySelector('#subtabs'); return e.className + ' ' + e.scrollWidth + '/' + e.clientWidth"));
  await js("document.querySelector('#btabs [data-tab=more]').click(); return true");
  await until("!document.querySelector('#pop').hidden", 3000);
  const more = await visible("#pop .opt");
  check("phone: More has Settings, the theme and resumegen, no keys", ["Settings", "theme", "Open resumegen"].every((w) => more.some((m) => m.includes(w)))
    && !more.some((m) => m.includes("Keyboard shortcuts")), S(more));     // W15: no keyboard items on a phone
  await js("[...document.querySelectorAll('#pop .opt')].find((o) => o.textContent.startsWith('Settings')).click(); return true");
  check("phone: Settings opens from More", await until(SET_OPEN, 6000));
  check("phone: Settings has no sideways scroll", await noSideways());
  await key("Escape");
  check("phone: Esc goes back from Settings", await until("document.body.dataset.view !== 'settings'", 4000));
  check("no page errors", errors.length === 0, errors.join(" | "));
} catch (err) {
  check("runner", false, err.stack || String(err));
} finally {
  console.log(JSON.stringify({ checks }));
  close();
}

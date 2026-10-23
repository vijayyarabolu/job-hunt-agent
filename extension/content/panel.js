// Job Hunt Agent Autofill, content script 5/n: the panel (top frame only), GOAL25 §2 "like Simplify". Docked on the right
// edge (340 px; the page moves over so nothing is under it), collapsible to a slim tab, or floating (draggable); the
// choice is remembered per site, and a narrow window opens it collapsed. It never covers the form's Next / Save
// buttons: a fixed footer bar shortens it, anything else under it collapses it to the tab. In its own closed shadow
// root (the page's styles never touch it) and its keys never reach the page. It only draws `state` and calls
// `actions`. Every status is an icon + a word; colors only blue / orange / red / grey.
"use strict";
var JH = globalThis.JH || (globalThis.JH = {});

(() => {
  const WIDTH = 340;
  const NARROW = 1000;
  const CLEAR_MS = 1500;
  const LAYOUT_KEY = "jh.layout";
  const PLACE_KEY = "jh.panel.place";
  const NAV = /^(next|next step|continue|save( (and|&) (continue|next))?|save|submit( (my )?application)?|review|apply( now)?|back|previous|finish)\b/i;
  const CSS = `
:host { all: initial !important; }
* { box-sizing: border-box; }
.box { position: fixed; z-index: 2147483647; display: flex; flex-direction: column; font: 13.5px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: var(--ink); background: var(--bg); border: 1px solid var(--line); overflow: hidden;
  --ink: #1d2129; --bg: #fff; --soft: #f4f6f9; --line: #d5d9df; --quiet: #5d6570;
  --blue: #1f5fbf; --blue-bg: #e8f0fd; --orange: #a85800; --orange-bg: #fff0df; --orange-line: #e58a2c;
  --grey: #374151; --grey-bg: #eef0f3; --grey2: #6b7280; --red: #7f1d1d; --red-bg: #fdecea; --on-blue: #fff; color-scheme: light; }
.box[hidden], .tab[hidden] { display: none; }
.box.dock { top: 0; right: 0; width: ${WIDTH}px; border-width: 0 0 0 1px; box-shadow: -6px 0 18px rgba(15,23,42,.10); }
.box.float { width: 320px; max-height: 76vh; border-radius: 12px; box-shadow: 0 10px 30px rgba(15,23,42,.18); }
.tab { position: fixed; z-index: 2147483647; right: 0; width: 34px; padding: 10px 0; display: flex; flex-direction: column; align-items: center; gap: 6px;
  background: #1f5fbf; color: #fff; border: 0; border-radius: 10px 0 0 10px; cursor: pointer; font: 600 12px/1.2 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  box-shadow: -3px 2px 10px rgba(15,23,42,.2); }
.tab .tv { writing-mode: vertical-rl; transform: rotate(180deg); letter-spacing: .3px; }
.tab .n { background: #fff; color: #a85800; border-radius: 9px; padding: 1px 5px; font-size: 11px; }
.head { display: flex; align-items: center; gap: 4px; padding: 8px 8px 8px 16px; border-bottom: 1px solid var(--line); user-select: none; }
.float .head { cursor: move; }
.brand { font-weight: 700; flex: 1; font-size: 13px; }
.who { font-size: 12px; background: var(--blue-bg); color: var(--blue); border-radius: 99px; padding: 0 8px; font-weight: 600; }
.ver { font-size: 11px; color: var(--quiet); margin-right: 4px; }
.icon { border: 0; background: transparent; font: inherit; font-size: 14px; line-height: 1; padding: 4px 6px; cursor: pointer; color: var(--quiet); border-radius: 6px; }
.icon:hover, .icon:focus-visible, .icon:active { background: var(--soft); color: var(--ink); }
.body { padding: 16px; overflow: auto; display: flex; flex-direction: column; gap: 16px; flex: 1; min-height: 0; }
.body > * { flex-shrink: 0; }
.job { display: flex; flex-direction: column; gap: 8px; }
.title { font-size: 15px; font-weight: 700; line-height: 1.3; overflow-wrap: anywhere; }
.strip { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { display: inline-flex; align-items: center; gap: 4px; border-radius: 99px; padding: 2px 10px; font-size: 12px; font-weight: 600; background: var(--grey-bg); color: var(--grey); }
.chip.ok { background: var(--blue-bg); color: var(--blue); } .chip.warn { background: var(--orange-bg); color: var(--orange); } .chip.bad { background: var(--red-bg); color: var(--red); }
.small { font-size: 12px; color: var(--quiet); }
.facts { display: flex; flex-direction: column; gap: 8px; padding: 8px 0 0; }
.fact { display: flex; flex-direction: column; gap: 0; }
.k { color: var(--quiet); font-size: 12px; }
.v { overflow-wrap: anywhere; }
a, .link { color: var(--blue); cursor: pointer; text-decoration: underline; background: none; border: 0; font: inherit; padding: 0; }
a:hover, .link:hover, .link:focus-visible, .link:active { color: var(--blue); background: transparent; text-decoration-thickness: 2px; }
.st { display: inline-flex; gap: 4px; align-items: center; border-radius: 6px; padding: 2px 8px; font-size: 12px; font-weight: 600; }
.st.needs { color: var(--orange); background: var(--orange-bg); } .st.filled { color: var(--blue); background: var(--blue-bg); }
.st.yours { color: var(--grey); background: var(--grey-bg); } .st.skipped { color: var(--grey2); background: var(--grey-bg); }
.st.review { color: var(--blue); background: var(--bg); border: 1px dashed var(--blue); }
.st.bad { color: var(--red); background: var(--red-bg); }
.notice { border-left: 3px solid var(--blue); padding: 4px 8px; color: var(--ink); background: var(--soft); border-radius: 0 6px 6px 0; overflow-wrap: anywhere; }
.notice.warn { border-left-color: var(--orange); } .notice.bad { border-left-color: var(--red); }
button.btn { font: inherit; font-size: 13px; border: 1px solid var(--line); background: var(--bg); color: var(--ink); border-radius: 8px; padding: 4px 10px; cursor: pointer; }
button.btn:hover, button.btn:focus-visible, button.btn:active { background: var(--soft); color: var(--ink); }
button.primary { background: var(--blue); border: 1px solid var(--blue); color: var(--on-blue); font: 700 15px/1.2 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; padding: 12px; width: 100%; border-radius: 10px; cursor: pointer; }
button.primary:hover, button.primary:focus-visible, button.primary:active { background: var(--blue); color: var(--on-blue); box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--blue); }
/* every control in every state reads at 4.5:1 (readable with red-green colour blindness): no fading, a dashed edge says disabled */
button:disabled, button.btn:disabled, button.primary:disabled, .icon:disabled, .link:disabled, li.f .q:disabled,
input:disabled, select:disabled, textarea:disabled { opacity: 1; background: var(--soft); color: var(--quiet); border: 1px dashed var(--quiet); box-shadow: none; cursor: not-allowed; }
button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible, summary:focus-visible { outline: 2px solid var(--blue); outline-offset: 1px; }
::placeholder { color: var(--quiet); opacity: 1; }
.toggle { display: flex; gap: 8px; align-items: center; font-size: 12px; color: var(--quiet); }
.counts { font-weight: 600; font-size: 12px; color: var(--quiet); }
details.more > summary { cursor: pointer; list-style: none; display: inline-flex; gap: 4px; align-items: center; color: var(--blue); font-size: 12px; font-weight: 600; }
summary::-webkit-details-marker { display: none; }
details.more[open] > summary .chev { transform: rotate(90deg); }
.chev { display: inline-block; transition: transform .15s; }
.group { display: flex; flex-direction: column; gap: 0; }
.ghead { display: flex; align-items: center; gap: 8px; padding: 8px 0 4px; border-bottom: 1px solid var(--line); }
ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
li.f { display: grid; grid-template-columns: 18px 1fr auto; gap: 0 8px; padding: 8px 0; border-bottom: 1px solid var(--soft); align-items: start; }
li.f .g { font-weight: 700; text-align: center; line-height: 1.45; }
li.f.needs .g { color: var(--orange); } li.f.filled .g, li.f.review .g { color: var(--blue); } li.f.yours .g, li.f.skipped .g { color: var(--grey2); }
li.f .q { cursor: pointer; text-align: left; background: none; border: 0; padding: 0; font: inherit; color: var(--ink); font-weight: 600; overflow-wrap: anywhere; }
li.f .q:hover, li.f .q:focus-visible, li.f .q:active { color: var(--ink); background: transparent; text-decoration: underline; }
li.f .val { grid-column: 2 / 4; color: var(--quiet); font-size: 12px; overflow-wrap: anywhere; }
li.f .acts { display: flex; gap: 4px; align-items: center; }
.edit { grid-column: 2 / 4; display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.edit input, .edit select, .edit textarea, .pick input, .addr input { width: 100%; padding: 6px 8px; border: 1px solid var(--line); border-radius: 6px; font: inherit; color: var(--ink); background: var(--bg); }
.edit textarea { min-height: 80px; resize: vertical; }
.snips { display: flex; flex-direction: column; gap: 4px; max-height: 180px; overflow: auto; }
.snips button { text-align: left; }
.pick { display: flex; flex-direction: column; gap: 8px; }
.pick li button { width: 100%; text-align: left; margin: 2px 0; }
.btns { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.btns button.btn > span[aria-hidden] { margin-right: 4px; }
.addr { border: 1px solid var(--line); background: var(--soft); border-radius: 8px; padding: 8px; display: flex; flex-direction: column; gap: 8px; }
.learned { display: flex; flex-direction: column; gap: 0; }
.lrow { display: flex; flex-direction: column; gap: 4px; padding: 8px 0; border-bottom: 1px solid var(--soft); }
.lrow .lq { font-weight: 600; overflow-wrap: anywhere; }
.lrow .la { color: var(--quiet); font-size: 12px; overflow-wrap: anywhere; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; }
.chips .chip { font-weight: 500; }
.cover { border: 1px solid var(--line); border-radius: 8px; padding: 8px; display: flex; flex-direction: column; gap: 8px; }
.cover .txt { white-space: pre-wrap; max-height: 140px; overflow: auto; font-size: 12px; color: var(--ink); }
.ans { grid-column: 2 / 4; border: 1px solid var(--line); border-radius: 8px; padding: 8px; margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }
.ans .txt { white-space: pre-wrap; max-height: 180px; overflow: auto; font-size: 12px; }
.ans-st { display: flex; gap: 6px; align-items: center; font-size: 12px; font-weight: 600; } .ans-st.ready { color: var(--blue); }
.ans-st.limit, .ans-st.login { color: var(--orange); } .ans-st.failed { color: var(--red); }
.ans-st.origin { color: var(--quiet); } .ans-st.names { color: var(--orange); }
/* GOAL30 §1.3: a question's own choices as one-click chips (fill the page field + save the answer) */
.choices { grid-column: 2 / 4; display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; align-items: center; }
button.pick { font: inherit; font-size: 12px; font-weight: 600; border: 1px solid var(--blue); color: var(--blue); background: var(--bg); border-radius: 99px;
  padding: 2px 10px; cursor: pointer; text-align: left; overflow-wrap: anywhere; }
button.pick:hover, button.pick:focus-visible, button.pick:active { background: var(--blue-bg); color: var(--blue); }
button.pick[aria-pressed=true] { background: var(--blue); color: var(--on-blue); }
.scope { display: flex; gap: 4px; align-items: center; font-size: 12px; color: var(--quiet); }
.scope select { font: inherit; font-size: 12px; padding: 2px 4px; border: 1px solid var(--line); border-radius: 6px; color: var(--ink); background: var(--bg); }
.loc { grid-column: 2 / 4; display: flex; flex-direction: column; align-items: flex-start; gap: 4px; margin-top: 8px; }
.dockfoot { border-top: 1px solid var(--line); padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; background: var(--bg); }
.tools { display: flex; gap: 8px; justify-content: space-between; }
.tools button.btn { flex: 1; display: inline-flex; align-items: center; justify-content: center; gap: 4px; padding: 6px 4px; font-size: 12px; color: var(--grey); }
.quiet { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; font-size: 12px; }
.quiet .link { color: var(--quiet); font-size: 12px; }
.slot:empty { display: none; }
/* GOAL24 §6: the H-1B badge (blue / grey / red, each with its own icon and word) */
.sb { display: inline-flex; align-items: center; gap: 4px; border-radius: 99px; padding: 1px 8px; font-size: 12px; font-weight: 700; border: 1.5px solid transparent; white-space: nowrap; }
.sb-i { font-size: 12px; line-height: 1; }
.sb-sponsors { background: #1f5fbf; color: #fff; }
.sb-cap_exempt { border-color: #1f5fbf; color: #1f5fbf; background: #fff; }
.sb-maybe { border-color: #6b7280; border-style: dashed; color: #374151; background: #fff; }
.sb-no { background: #b42318; color: #fff; }
.spons { display: flex; flex-direction: column; gap: 4px; }
@media (prefers-color-scheme: dark) {
  .sb-sponsors { background: #2563c9; } .sb-no { background: #c4372b; }
  .sb-cap_exempt { background: transparent; border-color: #8ab4f8; color: #8ab4f8; } .sb-maybe { background: transparent; border-color: #9aa3ae; color: #d1d5db; }
  .box { --ink: #e8eaee; --bg: #1d2126; --soft: #252a31; --line: #39404a; --quiet: #a2a9b4;
    --blue: #8db8ff; --blue-bg: #1d2b45; --orange: #ffb366; --orange-bg: #3a2614; --orange-line: #a8641f;
    --grey: #d0d4da; --grey-bg: #2a2f36; --grey2: #a2a9b4; --red: #ff9b8f; --red-bg: #3a1d1b; --on-blue: #10203d; color-scheme: dark; }
  .tab .n { color: #7a3f00; }
}`;
  const STATUS = { needs: ["▲", "Needs you"], review: ["?", "Review"], filled: ["✓", "Filled"], yours: ["✎", "Yours"], skipped: ["○", "Skipped"], bad: ["✕", "Problem"] };
  const ORDER = ["needs", "review", "filled", "yours", "skipped"];
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (k === "class") n.className = v;
      else if (k === "value") n.value = v;
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid.nodeType ? kid : String(kid));
    return n;
  };
  const btn = (label, onclick, cls = "btn", title) => el("button", { class: cls, onclick, title, type: "button" }, label);
  const iconBtn = (glyph, word, onclick, title) => el("button", { class: "btn", type: "button", onclick, title }, el("span", { "aria-hidden": "true" }, glyph), word);
  const status = (kind, word) => el("span", { class: `st ${kind}` }, el("span", { "aria-hidden": "true" }, STATUS[kind][0]), word || STATUS[kind][1]);

  // the sponsorship slot holds the job's H-1B badge (shape + word, never colour alone), the
  // future-sponsorship answer it will fill and why (this company's own answer first), and the "need it now" answer
  // (state.sponsor from job_hunt_agent's match). Replace JH.panelSlots.sponsorship to change it.
  const SB_ICON = { sponsors: "✓", cap_exempt: "★", maybe: "?", no: "✕" };
  JH.panelSlots = JH.panelSlots || {};
  JH.panelSlots.sponsorship = JH.panelSlots.sponsorship || ((s) => {
    const sp = s.sponsor;
    if (!sp || !s.job) return null;
    const b = sp.badge;
    const badge = b ? el("span", { class: `sb sb-${b.kind}`, title: b.why }, el("span", { class: "sb-i", "aria-hidden": "true" }, SB_ICON[b.kind] || "?"), b.text)
      : el("span", { class: "v small" }, "Nothing known about sponsorship");
    return el("div", { class: "spons" }, el("div", { class: "row" }, el("span", { class: "k" }, "Sponsorship"), badge),
      el("div", { class: "small" }, sp.future ? `Future sponsorship question: ${sp.future} (${sp.why})` : `Future sponsorship question: ${sp.why}`),
      el("div", { class: "small" }, sp.now ? `Sponsorship now: ${sp.now} (${sp.now_why || "your saved answer"})` : "Sponsorship now: not saved yet: it will ask you"));
  });

  let host = null, root = null, box = null, tab = null, state = {}, actions = {}, wanted = false;
  let pickQuery = "", editingAddress = false, editing = null, snips = null, typed = null, layout = { mode: "dock", collapsed: false };
  let bottomGap = 0, autoCollapsed = false;

  // ---- layout: dock / tab / float, per site --------------------------------------------------------------------
  async function loadLayout() {
    const got = await JH.store([LAYOUT_KEY]);
    const saved = (got[LAYOUT_KEY] || {})[location.host] || {};
    layout = { mode: saved.mode === "float" ? "float" : "dock", collapsed: Boolean(saved.collapsed) || innerWidth < NARROW };
  }
  async function saveLayout() {
    const got = await JH.store([LAYOUT_KEY]);
    JH.save({ [LAYOUT_KEY]: { ...(got[LAYOUT_KEY] || {}), [location.host]: { mode: layout.mode, collapsed: layout.collapsed } } });
  }
  function push(on) {                                // the page moves over, so the docked panel covers nothing
    let st = document.getElementById("jh-dock-style");
    if (!on) { if (st) st.remove(); return; }
    if (!st) {
      st = document.createElement("style");
      st.id = "jh-dock-style";
      (document.head || document.documentElement).append(st);
    }
    st.textContent = `html{margin-right:${WIDTH}px!important}`;
  }
  function placeFloat() {
    let saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(PLACE_KEY) || "null"); } catch { saved = null; }
    const p = saved || { right: 16, bottom: 16 };
    Object.assign(box.style, p.left != null ? { left: p.left + "px", top: p.top + "px", right: "auto", bottom: "auto" } : { right: p.right + "px", bottom: p.bottom + "px", left: "auto", top: "auto" });
  }
  function applyLayout() {
    if (!box) return;
    const docked = layout.mode === "dock";
    box.className = `box ${docked ? "dock" : "float"}`;
    box.hidden = layout.collapsed;
    tab.hidden = !layout.collapsed;
    box.removeAttribute("style");
    if (docked) box.style.height = `calc(100vh - ${bottomGap}px)`;
    else placeFloat();
    tab.style.top = `${Math.max(60, Math.round(innerHeight * 0.38))}px`;
    push(wanted && docked && !layout.collapsed);
  }
  // the site's own Next / Save / Submit stay clickable: a fixed footer shortens the panel; anything else -> the tab
  function keepClear() {
    if (!wanted || !box || box.hidden || layout.mode !== "dock") return;
    const panel = box.getBoundingClientRect();
    const hits = [...document.querySelectorAll("button, [role=button], input[type=submit], a")].slice(0, 600).filter((b) => {
      if (b.closest("[data-jh-panel]")) return false;
      const text = (JH.text(b) || b.value || b.getAttribute("aria-label") || "").trim();
      if (!NAV.test(text) && !/next|submit|footer/i.test(b.getAttribute("data-automation-id") || "")) return false;
      const r = b.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && r.right > panel.left && r.left < panel.right && r.bottom > panel.top && r.top < panel.bottom;
    });
    if (!hits.length) return;
    const pinned = (b) => { for (let n = b; n && n !== document.body; n = n.parentElement) if (["fixed", "sticky"].includes(getComputedStyle(n).position)) return true; return false; };
    if (hits.every(pinned)) {
      const top = Math.min(...hits.map((b) => b.getBoundingClientRect().top));
      bottomGap = Math.max(bottomGap, Math.ceil(innerHeight - top + 8));
    } else {
      layout = { ...layout, collapsed: true };
      autoCollapsed = true;
      render();
    }
    applyLayout();
  }
  setInterval(() => { if (wanted && host && !host.isConnected) document.documentElement.append(host); keepClear(); }, CLEAR_MS);

  function drag(head) {
    head.addEventListener("pointerdown", (e) => {
      if (layout.mode !== "float" || e.target.closest("button")) return;
      const r = box.getBoundingClientRect();
      const dx = e.clientX - r.left, dy = e.clientY - r.top;
      const move = (m) => Object.assign(box.style, { left: Math.min(Math.max(0, m.clientX - dx), innerWidth - 60) + "px",
        top: Math.min(Math.max(0, m.clientY - dy), innerHeight - 40) + "px", right: "auto", bottom: "auto" });
      const up = () => {
        removeEventListener("pointermove", move);
        removeEventListener("pointerup", up);
        try { sessionStorage.setItem(PLACE_KEY, JSON.stringify({ left: parseInt(box.style.left, 10), top: parseInt(box.style.top, 10) })); } catch { /* private window */ }
      };
      addEventListener("pointermove", move);
      addEventListener("pointerup", up);
    });
  }

  // ---- top: job, resume, address, sponsorship ------------------------------------------------------------------
  function refocus(sel) {
    const input = root.querySelector(sel);
    if (input) { input.focus(); if (input.setSelectionRange) input.setSelectionRange(input.value.length, input.value.length); }
  }
  // 1.3.1 (AUTOFILL-BUGS #8): the job as one line, a slim status strip, the facts behind "Details"
  function jobPart() {
    const s = state;
    if (s.job) {
      return el("div", { class: "job" }, el("div", { class: "title" }, `${s.job.company} · ${s.job.title}`), stripPart(),
        el("div", { class: "small" }, el("a", { href: "#", onclick: (e) => { e.preventDefault(); actions.notThis(); } }, "Not this job?")));
    }
    if (!s.loaded) return el("div", { class: "small" }, "Finding the job…");
    if (s.error) return null;
    const list = (s.recent || []).filter((r) => !pickQuery || `${r.company} ${r.title}`.toLowerCase().includes(pickQuery.toLowerCase()));
    return el("div", { class: "pick" },
      el("div", { class: "title" }, "Which job is this?"),
      el("div", { class: "small" }, "Pick its resume (remembered for this page)."),
      el("input", { placeholder: "Search company or title", value: pickQuery, "aria-label": "Search company or title", oninput: (e) => { pickQuery = e.target.value; render(); refocus(".pick input"); } }),
      el("ul", {}, list.slice(0, 8).map((r) => el("li", {}, btn(`${r.company} · ${r.title}${r.approved ? " ✓" : ""}`, () => actions.pick(r))))),
      !list.length ? el("div", { class: "small" }, s.recent && s.recent.length ? "Nothing matches." : "No resumes from the last 14 days.") : null);
  }
  // Resume ✓ · Sponsors ✓ · Match 79%: icon + word, blue / orange / red (never colour alone)
  const SPONSOR_CHIP = { sponsors: ["ok", "✓ Sponsors"], cap_exempt: ["ok", "★ Cap-exempt"], maybe: ["", "? Sponsorship"], no: ["bad", "✕ No sponsorship"] };
  function stripPart() {
    const s = state, out = [];
    const r = s.resume;
    if (r) out.push(r.kind === "approved" ? el("span", { class: "chip ok", title: r.label }, "✓ Resume")
      : r.kind === "ready" ? el("span", { class: "chip warn", title: r.warning || r.label }, "! Resume not approved")
      : r.kind === "base" ? el("span", { class: "chip", title: r.label }, "Base resume") : el("span", { class: "chip bad" }, "✕ No resume"));
    const b = s.sponsor && s.sponsor.badge;
    if (b && SPONSOR_CHIP[b.kind]) out.push(el("span", { class: `chip ${SPONSOR_CHIP[b.kind][0]}`, title: b.why || "" }, SPONSOR_CHIP[b.kind][1]));
    if (s.match && s.match.pct != null) out.push(el("span", { class: "chip ok", title: `Match with ${s.match.resume}` }, `Match ${s.match.pct}%`));
    return out.length ? el("div", { class: "strip" }, out) : null;
  }
  function resumePart() {
    const r = state.resume;
    if (!r) return null;
    return el("div", { class: "fact" }, el("span", { class: "k" }, "Resume"), el("span", { class: "v" }, `${r.label}${r.kind === "approved" ? " ✓ approved" : ""} `,
      state.job ? btn("View", () => actions.viewResume(), "link", "Open the PDF Autofill uploads for this job") : null),
      state.job && r.file_name ? el("div", { class: "btns" }, iconBtn("⤓", "Download resume", () => actions.downloadResume(), `Saves ${r.file_name}, the PDF Autofill uploads for this job`)) : null,
      r.warning ? el("span", { class: "small" }, r.warning) : null);
  }
  function addressEditor(a, city) {
    const street = el("input", { placeholder: "Street, e.g. 7480 Birdwood Ave (no apt)", value: a.street || "", "aria-label": "Street" });
    const zip = el("input", { placeholder: "ZIP", value: a.zip || "", inputmode: "numeric", maxlength: "5", "aria-label": "ZIP" });
    return el("div", { class: "addr" }, el("div", {}, `Address for ${city}`),
      a.kind === "failed" ? el("div", { class: "small" }, a.why) : null, street, zip,
      el("div", { class: "btns" }, btn("Save", () => { editingAddress = false; actions.address("use", { city_state: city, street: street.value, zip: zip.value }); }),
        a.kind === "failed" ? btn("Try OpenStreetMap again", () => actions.address("another", { city_state: city })) : btn("Cancel", () => { editingAddress = false; render(); })));
  }
  function addressPart() {
    const a = state.address;
    if (!a || !state.job) return null;
    const city = a.city_state;
    if (editingAddress && city) return addressEditor(a, city);
    // user 2026-10-14: never a question. The tracker row's address, else home / saved, else the best OpenStreetMap
    // address picked and saved at once; a small Change link replaces it
    const change = city && a.kind !== "home" ? btn("Change", () => { editingAddress = true; render(); }, "link", `Another street for ${city}`) : null;
    if (a.line) return el("div", { class: "fact" }, el("span", { class: "k" }, "Address"), el("span", { class: "v" }, `${a.line} `, change));
    const why = a.kind === "looking" ? `${city} (finding a street on OpenStreetMap…)` : a.kind === "failed" ? `${city} (no street found: city and state are filled)` : a.why || city;
    return el("div", { class: "fact" }, el("span", { class: "k" }, "Address"), el("span", { class: "v" }, `${why} `, change));
  }
  // the facts behind one toggle (open by itself while something there needs the user: an address to fix, a warning)
  let detailsOpen = null;
  function detailsPart() {
    const s = state;
    if (!s.job) return null;
    const how = { picked: "Matched by your pick", tab: "The posting you opened in this tab" }[s.job.how];
    const urgent = editingAddress || (s.resume && s.resume.warning) || (s.address && s.address.kind === "failed");
    const open = detailsOpen == null ? Boolean(urgent) : detailsOpen;
    const extra = noticeItems().slice(1);              // notes the one notice line could not show
    return el("details", { class: "more", open: open || null, ontoggle: (e) => { detailsOpen = e.target.open; } },
      el("summary", {}, el("span", { class: "chev", "aria-hidden": "true" }, "›"), "Details"),
      el("div", { class: "facts" }, how ? el("div", { class: "small" }, how) : null, resumePart(), addressPart(), slot("sponsorship"),
        extra.map((n) => el("div", { class: "small" }, n.text))));
  }
  function slot(name) {
    let got = null;
    try { got = JH.panelSlots[name] ? JH.panelSlots[name](state) : null; } catch { got = null; }
    return el("div", { class: `slot slot-${name}`, "data-slot": name }, got);
  }

  // ---- the field list: Needs you, Filled, Yours, Skipped --------------------------------------------------------
  function snipList(r, input) {
    if (!snips || snips.id !== r.id) return null;
    if (!snips.items.length) return el("div", { class: "small" }, snips.loading ? "Loading…" : "No saved answers yet.");
    return el("div", { class: "snips" }, snips.items.map((x) => btn(`${x.company ? x.company + ": " : ""}${x.text.slice(0, 90)}${x.text.length > 90 ? "…" : ""}`,
      () => { input.value = x.text; typed = { id: r.id, value: x.text }; }, "btn", x.question)));
  }
  function editor(r) {
    const choices = r.options || [];
    const input = choices.length
      ? el("select", { "aria-label": `New answer for ${r.label}` }, el("option", { value: "" }, "Choose…"), choices.map((o) => el("option", { value: o }, o)))
      : el(r.kind === "textarea" ? "textarea" : "input", { "aria-label": `New answer for ${r.label}` });
    const start = typed && typed.id === r.id ? typed.value : r.value || "";   // what the user typed survives the list refreshing
    input.value = input.tagName !== "SELECT" || choices.includes(start) ? start : "";
    input.addEventListener("input", () => { typed = { id: r.id, value: input.value }; });
    input.addEventListener("change", () => { typed = { id: r.id, value: input.value }; });
    // an open question's text is kept with this application when the user leaves the page (GOAL25: "Saved with this
    // application", Undo) and offered elsewhere; every other row is saved now, where the user chose
    const open = JH.answers.isOpen(r);
    const scope = open ? null : scopePart(r);
    const save = () => { const v = input.value; editing = null; snips = null; typed = null; if (String(v).trim() && !open) actions.keep(r, v, scopeOf(r)); else actions.setField(r, v); };
    if (input.tagName === "INPUT") input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
    const more = async () => { snips = { id: r.id, items: [], loading: true }; render(); snips = { id: r.id, items: await actions.snippets(r) }; render(); };
    return el("div", { class: "edit" }, input, snipList(r, input), scope,
      el("div", { class: "btns" }, btn("Fill it", save, "btn", "Puts this into the page's field (and keeps it for next time)"),
        btn("Cancel", () => { editing = null; snips = null; typed = null; render(); }),
        r.kind === "textarea" ? btn("Use a saved answer ▾", more, "link", "Answers you wrote for other applications") : null));
  }
  const GLYPH = { needs: "▲", review: "?", filled: "✓", yours: "✎", skipped: "○" };   // GOAL30 §6: no dash in the panel, not even as an icon
  // GOAL30 §4: where a chip or a ✎ answer is saved; Location and open questions default to this job only
  const PLACE = new Set(["location", "city", "state", "zip", "address1"]);
  const SCOPES = [["everywhere", "everywhere"], ["company", "this company only"], ["job", "this job only"]];
  const scopes = new Map();                            // row id -> the user's pick in "Save for"
  const scopeOf = (r) => scopes.get(r.id) || (PLACE.has(r.key) || JH.answers.isOpen(r) ? "job" : "everywhere");
  function scopePart(r) {
    const sel = el("select", { "aria-label": `Save the answer to ${r.label} for` }, SCOPES.map(([v, w]) => el("option", { value: v }, w)));
    sel.value = scopeOf(r);
    sel.addEventListener("change", () => scopes.set(r.id, sel.value));
    return el("label", { class: "scope" }, "Save for", sel);
  }
  const CHIPS = 12;
  const allChips = new Set();
  function choicesPart(r) {                             // GOAL30 §1.3: no saved answer: the page's own choices, one click
    const opts = (r.options || []).filter((o) => o && !/^(select one|select|choose|--+.*)$/i.test(o.trim()));
    if (r.status !== "needs" || !opts.length || editing === r.id) return null;
    const shown = allChips.has(r.id) ? opts : opts.slice(0, CHIPS);
    return el("div", { class: "choices", role: "group", "aria-label": `Choices for ${r.label}` },
      shown.map((o) => el("button", { class: "pick", type: "button", title: `Fill “${o}” and save it`, onclick: () => actions.keep(r, o, scopeOf(r)) }, o)),
      shown.length < opts.length ? btn(`+${opts.length - shown.length} more`, () => { allChips.add(r.id); render(); }, "link", "Show every choice") : null,
      scopePart(r));
  }
  // GOAL30 §3: a Location field says where its value comes from, and switches with one click (this job only)
  function locationPart(r) {
    const loc = state.location;
    if (!loc || !(r.key === "location" || r.key === "city") || !loc.resume) return null;
    const home = loc.choice === "home";
    const chip = (on, text, choice, title) => el("button", { class: "pick", type: "button", "aria-pressed": on ? "true" : "false", title,
      onclick: () => { if (!on) actions.location(choice); } }, on ? `✓ ${text}` : text);
    if (!loc.home || loc.home === loc.resume) return el("div", { class: "loc small" }, `From your resume for this job: ${loc.resume}`);
    return el("div", { class: "loc choices", role: "group", "aria-label": `Where ${r.label} comes from` },   // the ✓ one is used
      chip(!home, `From your resume for this job: ${loc.resume}`, "resume", "The city on the resume you upload for this job"),
      chip(home, `Where you live: ${loc.home}`, "home", "Your own city, for this job only"));
  }
  // GOAL30 §5: the button the user pressed says "Writing with Claude…" in this frame; the panel's full redraw (and the
  // request) follow once that frame is painted
  function writeSoon(r, variant, e) {
    const b = e && e.currentTarget;
    if (b) { b.textContent = "Writing with Claude…"; b.disabled = true; }
    requestAnimationFrame(() => setTimeout(() => actions.generate(r, variant), 0));
  }
  function answerPart(r, view) {                 // GOAL29 §5: Claude's answer to this open question (answers.js)
    const h = { generate: (variant, e) => writeSoon(r, variant, e), copy: () => { navigator.clipboard.writeText(view.text).catch(() => {}); },
      fill: () => actions.fillAnswer(r, view.text), save: () => actions.saveAnswer(r, view.text) };
    return JH.answers.block(view, h, el, btn);
  }
  function filledPart(r) {                       // GOAL30 §2: an open question that already holds text
    const flags = (state.filled || {})[r.label];
    const h = { generate: (variant, e) => writeSoon(r, variant, e), copy: () => { navigator.clipboard.writeText(r.value).catch(() => {}); },
      fill: () => actions.fillAnswer(r, r.value), save: () => actions.saveAnswer(r, r.value) };
    return JH.answers.filledBlock(r, flags, h, el, btn);
  }
  function fieldRow(r) {
    const open = editing === r.id;
    const answer = (state.answers || {})[r.label];
    const openQ = JH.answers.isOpen(r);
    return el("li", { class: `f ${r.status}`, "data-id": r.id },
      el("span", { class: "g", "aria-hidden": "true" }, GLYPH[r.status] || "·"),
      el("button", { class: "q", type: "button", title: "Show it on the page", onclick: () => actions.focus(r) }, r.label || "(a question)"),
      el("span", { class: "acts" }, !answer && openQ && !r.value ? btn("Generate", (e) => writeSoon(r, "", e), "link", "Claude writes an answer from the JD, your resume and your facts (nothing is filled until you click Fill this field)") : null,
        r.kind !== "file" ? btn("✎", () => { editing = open ? null : r.id; snips = null; typed = null; render(); refocus(".edit input, .edit textarea, .edit select"); }, "icon", `Edit ${r.label}`) : null),
      el("span", { class: "val" }, r.value ? String(r.value).slice(0, 140) : r.status === "needs" ? "No saved answer" : "Empty"),
      locationPart(r), choicesPart(r),
      answer ? answerPart(r, answer) : openQ && r.value ? filledPart(r) : null, open ? editor(r) : null);
  }
  // results as one clean list per group; a long group shows its first rows and "+N more"
  const SHOW = 8;
  const expanded = new Set();
  function fieldsPart() {
    const rows = state.rows || [];
    if (!rows.length) return null;
    return ORDER.map((kind) => {
      const list = rows.filter((r) => r.status === kind);
      if (!list.length) return null;
      const all = expanded.has(kind) || list.length <= SHOW + 2 || list.some((r) => r.id === editing);
      const shown = all ? list : list.slice(0, kind === "skipped" ? 3 : SHOW);
      return el("div", { class: "group", "data-group": kind },
        el("div", { class: "ghead" }, status(kind, `${STATUS[kind][1]} (${list.length})`)),
        el("ul", {}, shown.map(fieldRow)),
        shown.length < list.length ? btn(`+${list.length - shown.length} more`, () => { expanded.add(kind); render(); }, "link", `Show every ${STATUS[kind][1]} field`) : null);
    });
  }
  // 1.3.1 (AUTOFILL-BUGS #5): each saved answer gets the full width: the question, then the answer, then what it is + Undo
  function learnedPart() {
    const items = state.learned || [];
    if (!items.length) return null;
    const what = (x) => (x.scope === "company" ? `Saved for ${x.company} only` : x.scope === "snippet" ? "Saved with this application" : "Saved for next time");
    return el("div", { class: "learned", role: "status" }, items.slice(-6).map((x) => el("div", { class: "lrow" },
      el("div", { class: "lq" }, x.label),
      el("div", { class: "la" }, `= ${String(x.answer).slice(0, 120)}${String(x.answer).length > 120 ? "…" : ""}`),
      el("div", { class: "btns" }, status("filled", what(x)),
        x.undone ? el("span", { class: "small" }, "Undone") : btn("Undo", () => actions.undo(x), "link", "Forget this answer (the earlier one comes back)")))));
  }
  // GOAL27 §6: the missing keywords (the % is in the status strip)
  function matchPart() {
    const m = state.match;
    if (!m) return null;
    if (m.error) return el("div", { class: "small" }, `Match: ${m.error}`);
    return el("div", { class: "fact" }, el("span", { class: "k" }, `Match ${m.pct}% with ${m.resume}`),
      m.missing && m.missing.length ? el("div", { class: "chips", "aria-label": "Missing keywords" }, el("span", { class: "small" }, "Missing keywords:"),
        m.missing.slice(0, 12).map((k) => el("span", { class: "chip warn" }, k))) : el("span", { class: "small" }, "No missing keywords"));
  }
  // GOAL27 §5-§6: the letter the user asked for (Paste / Upload as PDF / Download PDF / Copy); the tool buttons sit in the
  // footer. GOAL34 §2: a site whose upload box ignores Autofill's file gets Download PDF and why, not Upload as PDF
  function coverPart() {
    const cover = state.cover;
    if (!cover || !cover.text) return null;
    const refused = JH.noUpload();
    return el("div", { class: "cover" }, el("div", { class: "small" }, `Cover letter (${cover.file_name})`), el("div", { class: "txt" }, cover.text),
      refused ? el("div", { class: "small", role: "note" }, `${refused} doesn't accept uploads from Autofill: use Download PDF and drop it in`) : null,
      el("div", { class: "btns" }, iconBtn("↧", "Paste into the form", () => actions.coverInto("text")),
        refused ? null : iconBtn("⤒", "Upload as PDF", () => actions.coverInto("pdf")),
        iconBtn("⤓", "Download PDF", () => actions.downloadCover(), `Saves ${cover.file_name}`),
        btn("Copy", () => { navigator.clipboard.writeText(cover.text).catch(() => {}); }, "link")));
  }
  function toolsPart() {
    const s = state;
    return el("div", { class: "tools" },
      iconBtn("✉", s.coverBusy ? "Writing…" : "Cover letter", () => actions.cover(), "Claude writes one from your tailored resume and this job's description"),
      s.job && s.job.app ? null : iconBtn("☆", s.saved ? "Saved ✓" : "Save job", () => actions.save(), "Adds this job to your tracker as Saved"),
      iconBtn("%", "Match", () => actions.match(), "Match % and missing keywords against your resumes"));
  }
  // what the one notice line says, most important first
  function noticeItems() {
    const s = state, out = [];
    if (s.stale) return [{ kind: "warn", text: s.stale }];            // GOAL32 §3: nothing else works until a refresh
    if (s.health && !s.health.ok) out.push({ kind: "bad", text: s.health.message, head: "Job Hunt Agent is not answering", settings: true });
    if (s.error) out.push({ kind: "bad", text: s.error, settings: true });
    if (s.captcha) out.push({ kind: "warn", text: "Solve the check on the page, then click Autofill" });
    if (s.message) out.push({ kind: "", text: s.message });
    if (s.update) out.push({ kind: "warn", text: s.update });
    return out;
  }
  function noticePart() {
    const n = noticeItems()[0];
    if (!n) return null;
    return el("div", { class: `notice ${n.kind}`, role: "status" }, n.head ? [status("bad", n.head), " "] : null, n.text,
      n.settings ? [" ", btn("Settings", () => actions.settings(), "link")] : null);
  }
  // "Skills: 2 added · not in Workday's list: X, Y" -> the words, then each skill as a chip
  function messageWithChips(text) {
    const m = /^(.*?(?:not in (?:the|Workday's) list|Skills not in the list):)\s*(.+)$/.exec(text);
    if (!m) return text;
    return [m[1], " ", el("span", { class: "chips" }, m[2].split(/,\s*/).map((k) => el("span", { class: "chip warn" }, k)))];
  }
  function headPart() {
    const s = state;
    const docked = layout.mode === "dock";
    const head = el("div", { class: "head" }, el("span", { class: "brand" }, "Job Hunt Agent Autofill"),
      s.profileName ? el("span", { class: "who", title: "Profile (change it in the extension's settings)" }, s.profileName) : null,
      s.version ? el("span", { class: "ver" }, `v${s.version}`) : null,
      btn(docked ? "⇱" : "⇥", () => { layout = { ...layout, mode: docked ? "float" : "dock" }; saveLayout(); applyLayout(); render(); }, "icon", docked ? "Float: a small box you can drag" : "Dock on the right edge"),
      btn("›", () => { layout = { ...layout, collapsed: true }; saveLayout(); applyLayout(); render(); }, "icon", "Collapse to a slim tab"),
      btn("×", () => JH.panel.hide(), "icon", "Hide (the toolbar button shows it again)"));
    drag(head);
    return head;
  }

  // a re-render (the list refreshes as the user works) keeps the list's scroll and the box the user is typing in
  const nameOf = (n) => (n && (n.getAttribute("aria-label") || n.getAttribute("title"))) || "";
  function keepPlace() {
    const old = box.querySelector(".body");
    const top = old ? old.scrollTop : 0;
    const active = root.activeElement;
    const name = active && box.contains(active) ? nameOf(active) : "";
    const sel = name && active.selectionStart != null ? [active.selectionStart, active.selectionEnd] : null;
    return (body) => {
      if (top) body.scrollTop = top;                   // GOAL30 §5: at the top already: no forced layout on each redraw
      if (!name) return;
      const again = [...box.querySelectorAll("[aria-label], [title]")].find((n) => nameOf(n) === name);
      if (!again) return;
      again.focus({ preventScroll: true });
      if (sel && again.setSelectionRange) try { again.setSelectionRange(sel[0], sel[1]); } catch { /* a select has no selection */ }
    };
  }
  function render() {
    if (!root) return;
    const s = state;
    const c = s.counts;
    const notice = noticePart();
    if (notice && s.message && notice.textContent.includes(s.message)) {        // skills as chips, not a run-on sentence
      const parts = String(s.message).split(" · ");
      if (parts.some((x) => /not in (the|Workday's) list/.test(x))) {
        notice.replaceChildren(...parts.flatMap((x, i) => [i ? " · " : "", ...[].concat(messageWithChips(x))]));
      }
    }
    const body = el("div", { class: "body" },
      jobPart(), detailsPart(), notice,
      c ? el("div", { class: "counts" }, `Filled ${c.filled} · Needs you ${c.needs} · Skipped ${c.skipped}${c.review ? ` · Review ${c.review}` : ""}`) : null,
      c && c.review ? el("div", { class: "small" }, "? Review: answered from your resume by Claude. Read them before you go on.") : null,
      c && c.notes ? el("div", { class: "small" }, c.notes) : null,
      matchPart(),
      coverPart(), learnedPart(), fieldsPart(),
      s.applied ? status("filled", s.applied) : null);
    const foot = el("div", { class: "dockfoot" },
      btn(s.busy ? "Filling…" : "Autofill", () => actions.autofill(), "primary"),
      toolsPart(),
      el("div", { class: "quiet" },
        el("label", { class: "toggle" }, el("input", { type: "checkbox", checked: s.auto, onchange: (e) => actions.auto(e.target.checked) }), "Auto-fill each new page"),
        btn("Mark applied", () => actions.applied(), "link", "Same as Mark applied in Job Hunt Agent"),
        btn("Send page snapshot", () => actions.snapshot(), "link", "Sends this form's HTML (no passwords) so a failing page can be fixed"),
        s.openNeeds ? el("span", { class: "small" }, `${s.openNeeds} question(s) wait in Job Hunt Agent › Answers`) : null));
    const kept = keepPlace();
    box.replaceChildren(headPart(), body, foot);
    kept(body);
    const needs = (s.rows || []).filter((r) => r.status === "needs").length || (c && c.needs) || 0;
    const m = s.match && s.match.pct != null ? s.match : null;
    // replaceChildren would show a null as the word "null": leave the empty parts out
    tab.replaceChildren(...[el("span", { class: "tv" }, "Autofill"), needs ? el("span", { class: "n", title: `${needs} need you` }, `${needs} ▲`) : null,
      m ? el("span", { class: "n", title: `Match with ${m.resume}` }, `${m.pct}%`) : null].filter(Boolean));
    tab.title = autoCollapsed ? "Collapsed so the page's buttons stay clear: click to open" : "Open the Autofill panel";
    tab.setAttribute("aria-label", `Open the Autofill panel${needs ? ` (${needs} need you)` : ""}`);
  }
  JH.panel = {
    async show() {
      if (!JH.isTop) return;
      if (!host) {
        host = document.createElement("div");
        host.setAttribute("data-jh-panel", "");
        for (const type of ["keydown", "keyup", "keypress", "input", "beforeinput"]) host.addEventListener(type, (e) => e.stopPropagation());
        root = host.attachShadow({ mode: "closed" });
        root.append(el("style", {}, CSS));
        box = el("div", { class: "box dock", role: "complementary", "aria-label": "Job Hunt Agent Autofill" });
        tab = el("button", { class: "tab", type: "button", hidden: true, onclick: () => {
          layout = { ...layout, collapsed: false }; autoCollapsed = false; bottomGap = 0; saveLayout(); applyLayout(); render(); keepClear(); } });
        root.append(box, tab);
        await loadLayout();
        addEventListener("resize", () => { bottomGap = 0; applyLayout(); keepClear(); });
      }
      if (!host.isConnected) document.documentElement.append(host);
      wanted = true;
      render();
      applyLayout();
      keepClear();
    },
    // GOAL27 §6: a job posting shows the slim tab with its match % (the full panel opens on a click), unless the user chose
    // a layout for this site
    async peek() {
      const got = await JH.store([LAYOUT_KEY]);
      const own = (got[LAYOUT_KEY] || {})[location.host];
      await JH.panel.show();
      if (!own) { layout = { ...layout, collapsed: true }; applyLayout(); render(); }
    },
    hide() { wanted = false; push(false); if (host) host.remove(); },
    toggle() { if (host && host.isConnected) JH.panel.hide(); else JH.panel.show(); },
    shown: () => Boolean(host && host.isConnected),
    layout: () => ({ ...layout, rect: box && !box.hidden ? box.getBoundingClientRect().toJSON() : null }),
    set(patch) { state = { ...state, ...patch }; render(); },
    get: () => state,
    on(handlers) { actions = handlers; },
  };
})();

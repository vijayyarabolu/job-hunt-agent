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
.box { position: fixed; z-index: 2147483647; display: flex; flex-direction: column; font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
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
.head { display: flex; align-items: center; gap: 6px; padding: 8px 10px; background: var(--soft); border-bottom: 1px solid var(--line); user-select: none; }
.float .head { cursor: move; }
.brand { font-weight: 700; flex: 1; }
.who { font-size: 12px; background: var(--blue-bg); color: var(--blue); border-radius: 99px; padding: 1px 8px; font-weight: 600; }
.ver { font-size: 11px; color: var(--quiet); }
.icon { border: 0; background: transparent; font: inherit; font-size: 14px; line-height: 1; padding: 3px 5px; cursor: pointer; color: var(--quiet); border-radius: 6px; }
.icon:hover, .icon:focus-visible, .icon:active { background: var(--soft); color: var(--ink); }
.body { padding: 10px 12px 14px; overflow: auto; display: flex; flex-direction: column; gap: 8px; flex: 1; min-height: 0; }
.body > * { flex-shrink: 0; }
.row { display: flex; gap: 6px; align-items: baseline; }
.k { color: var(--quiet); min-width: 76px; font-size: 12px; }
.v { flex: 1; font-weight: 500; overflow-wrap: anywhere; }
.facts { display: flex; flex-direction: column; gap: 4px; border: 1px solid var(--line); border-radius: 8px; padding: 8px; }
a, .link { color: var(--blue); cursor: pointer; text-decoration: underline; background: none; border: 0; font: inherit; padding: 0; }
a:hover, .link:hover, .link:focus-visible, .link:active { color: var(--blue); background: transparent; text-decoration-thickness: 2px; }
.st { display: inline-flex; gap: 4px; align-items: center; border-radius: 6px; padding: 3px 7px; font-size: 12px; font-weight: 600; }
.st.needs { color: var(--orange); background: var(--orange-bg); } .st.filled { color: var(--blue); background: var(--blue-bg); }
.st.yours { color: var(--grey); background: var(--grey-bg); } .st.skipped { color: var(--grey2); background: var(--grey-bg); }
.st.bad { color: var(--red); background: var(--red-bg); }
.msg { background: var(--blue-bg); color: var(--blue); border-radius: 6px; padding: 6px 8px; font-weight: 600; }
.warn { color: var(--orange); background: var(--orange-bg); border-radius: 6px; padding: 4px 7px; font-size: 12px; }
button.btn { font: inherit; border: 1px solid var(--line); background: var(--bg); color: var(--ink); border-radius: 7px; padding: 4px 9px; cursor: pointer; }
button.btn:hover, button.btn:focus-visible, button.btn:active { background: var(--soft); color: var(--ink); }
button.primary { background: var(--blue); border-color: var(--blue); color: var(--on-blue); font-weight: 700; font-size: 15px; padding: 9px 10px; width: 100%; }
button.primary:hover, button.primary:focus-visible, button.primary:active { background: var(--blue); color: var(--on-blue); box-shadow: 0 0 0 2px var(--bg), 0 0 0 4px var(--blue); }
/* every control in every state reads at 4.5:1 (readable with red-green colour blindness): no fading, a dashed edge says disabled */
button:disabled, button.btn:disabled, button.primary:disabled, .icon:disabled, .link:disabled, li.f .q:disabled,
input:disabled, select:disabled, textarea:disabled { opacity: 1; background: var(--soft); color: var(--quiet); border: 1px dashed var(--quiet); box-shadow: none; cursor: not-allowed; }
button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 2px solid var(--blue); outline-offset: 1px; }
::placeholder { color: var(--quiet); opacity: 1; }
.toggle { display: flex; gap: 6px; align-items: center; font-size: 12px; }
.counts { font-weight: 600; font-size: 12px; color: var(--quiet); }
details { border: 1px solid var(--line); border-radius: 8px; }
summary { cursor: pointer; padding: 6px 8px; list-style: none; display: flex; align-items: center; gap: 6px; }
summary::-webkit-details-marker { display: none; }
ul { list-style: none; margin: 0; padding: 0 6px 6px; display: flex; flex-direction: column; gap: 3px; }
li.f { display: grid; grid-template-columns: 1fr auto; gap: 2px 6px; padding: 5px 6px; border-radius: 6px; border: 1px solid transparent; }
li.f:hover { background: var(--soft); }
li.f.needs { border-color: var(--orange-line); background: var(--orange-bg); }
li.f .q { cursor: pointer; text-align: left; background: none; border: 0; padding: 0; font: inherit; color: var(--ink); font-weight: 600; overflow-wrap: anywhere; }
li.f .q:hover, li.f .q:focus-visible, li.f .q:active { color: var(--ink); background: transparent; text-decoration: underline; }
li.f .val { grid-column: 1 / -1; color: var(--quiet); font-size: 12px; overflow-wrap: anywhere; }
li.f .acts { display: flex; gap: 4px; align-items: center; }
.edit { grid-column: 1 / -1; display: flex; flex-direction: column; gap: 6px; margin-top: 4px; }
.edit input, .edit select, .edit textarea, .pick input, .addr input { width: 100%; padding: 5px 7px; border: 1px solid var(--line); border-radius: 6px; font: inherit; color: var(--ink); background: var(--bg); }
.edit textarea { min-height: 80px; resize: vertical; }
.snips { display: flex; flex-direction: column; gap: 4px; max-height: 180px; overflow: auto; }
.snips button { text-align: left; }
.pick ul { padding: 0; }
.pick li button { width: 100%; text-align: left; }
.small { font-size: 12px; color: var(--quiet); }
.btns { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.addr { border: 1px solid var(--line); background: var(--blue-bg); border-radius: 8px; padding: 7px; display: flex; flex-direction: column; gap: 6px; }
.learned { border: 1px solid var(--line); border-radius: 8px; padding: 6px 8px; display: flex; flex-direction: column; gap: 4px; }
.foot { display: flex; gap: 6px; flex-wrap: wrap; border-top: 1px solid var(--line); padding-top: 8px; }
.slot:empty { display: none; }
/* GOAL24 §6: the H-1B badge (blue / grey / red, each with its own icon and word) */
.sb { display: inline-flex; align-items: center; gap: 4px; border-radius: 99px; padding: 1px 8px; font-size: 12px; font-weight: 700; border: 1.5px solid transparent; white-space: nowrap; }
.sb-i { font-size: 12px; line-height: 1; }
.sb-sponsors { background: #1f5fbf; color: #fff; }
.sb-cap_exempt { border-color: #1f5fbf; color: #1f5fbf; background: #fff; }
.sb-maybe { border-color: #6b7280; border-style: dashed; color: #374151; background: #fff; }
.sb-no { background: #b42318; color: #fff; }
.spons { display: flex; flex-direction: column; gap: 3px; }
@media (prefers-color-scheme: dark) {
  .sb-sponsors { background: #2563c9; } .sb-no { background: #c4372b; }
  .sb-cap_exempt { background: transparent; border-color: #8ab4f8; color: #8ab4f8; } .sb-maybe { background: transparent; border-color: #9aa3ae; color: #d1d5db; }
  .box { --ink: #e8eaee; --bg: #1d2126; --soft: #252a31; --line: #39404a; --quiet: #a2a9b4;
    --blue: #8db8ff; --blue-bg: #1d2b45; --orange: #ffb366; --orange-bg: #3a2614; --orange-line: #a8641f;
    --grey: #d0d4da; --grey-bg: #2a2f36; --grey2: #a2a9b4; --red: #ff9b8f; --red-bg: #3a1d1b; --on-blue: #10203d; color-scheme: dark; }
}`;
  const STATUS = { needs: ["▲", "Needs you"], filled: ["✓", "Filled"], yours: ["✎", "Yours"], skipped: ["–", "Skipped"], bad: ["✕", "Problem"] };
  const ORDER = ["needs", "filled", "yours", "skipped"];
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
    chrome.storage.local.set({ [LAYOUT_KEY]: { ...(got[LAYOUT_KEY] || {}), [location.host]: { mode: layout.mode, collapsed: layout.collapsed } } });
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
  function jobPart() {
    const s = state;
    if (s.job) {
      const how = { picked: "Matched by your pick", tab: "The posting you opened in this tab" }[s.job.how];
      return [el("div", { class: "row" }, el("span", { class: "k" }, "Job"), el("span", { class: "v" }, `${s.job.company} · ${s.job.title}`)),
        el("div", { class: "small" }, how ? `${how} · ` : "", el("a", { href: "#", onclick: (e) => { e.preventDefault(); actions.notThis(); } }, "Not this job?"))];
    }
    if (!s.loaded) return el("div", { class: "small" }, "Finding the job…");
    if (s.error) return null;
    const list = (s.recent || []).filter((r) => !pickQuery || `${r.company} ${r.title}`.toLowerCase().includes(pickQuery.toLowerCase()));
    return el("div", { class: "pick" },
      el("div", { class: "warn" }, "Which job is this? Pick its resume (remembered for this page)."),
      el("input", { placeholder: "Search company or title", value: pickQuery, "aria-label": "Search company or title", oninput: (e) => { pickQuery = e.target.value; render(); refocus(".pick input"); } }),
      el("ul", {}, list.slice(0, 8).map((r) => el("li", {}, btn(`${r.company} · ${r.title}${r.approved ? " ✓" : ""}`, () => actions.pick(r))))),
      !list.length ? el("div", { class: "small" }, s.recent && s.recent.length ? "Nothing matches." : "No resumes from the last 14 days.") : null);
  }
  function resumePart() {
    const r = state.resume;
    if (!r) return null;
    return [el("div", { class: "row" }, el("span", { class: "k" }, "Resume"), el("span", { class: "v" }, `${r.label}${r.kind === "approved" ? " ✓ approved" : ""} `,
      state.job ? btn("View", () => actions.viewResume(), "link", "Open the PDF Autofill uploads for this job") : null)),
      r.warning ? el("div", { class: "warn" }, r.warning) : null];
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
    if (a.line) return el("div", { class: "row" }, el("span", { class: "k" }, "Address"), el("span", { class: "v" }, `${a.line} `, change));
    const why = a.kind === "looking" ? `${city} (finding a street on OpenStreetMap…)` : a.kind === "failed" ? `${city} (no street found: city and state are filled)` : a.why || city;
    return el("div", { class: "row" }, el("span", { class: "k" }, "Address"), el("span", { class: "v" }, `${why} `, change));
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
    const save = () => { const v = input.value; editing = null; snips = null; typed = null; actions.setField(r, v); };
    if (input.tagName === "INPUT") input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
    const more = async () => { snips = { id: r.id, items: [], loading: true }; render(); snips = { id: r.id, items: await actions.snippets(r) }; render(); };
    return el("div", { class: "edit" }, input, snipList(r, input),
      el("div", { class: "btns" }, btn("Fill it", save, "btn", "Puts this into the page's field (and keeps it for next time)"),
        btn("Cancel", () => { editing = null; snips = null; typed = null; render(); }),
        r.kind === "textarea" ? btn("Use a saved answer ▾", more, "link", "Answers you wrote for other applications") : null));
  }
  function fieldRow(r) {
    const open = editing === r.id;
    return el("li", { class: `f ${r.status}`, "data-id": r.id },
      el("button", { class: "q", type: "button", title: "Show it on the page", onclick: () => actions.focus(r) }, r.label || "(a question)"),
      el("span", { class: "acts" }, r.kind === "textarea" && r.status === "needs" ? btn("Draft", () => actions.draft(r), "link", "Claude writes 2-4 sentences from the JD and your resume") : null,
        r.kind !== "file" ? btn("✎", () => { editing = open ? null : r.id; snips = null; typed = null; render(); refocus(".edit input, .edit textarea, .edit select"); }, "icon", `Edit ${r.label}`) : null),
      el("span", { class: "val" }, r.value ? String(r.value).slice(0, 140) : r.status === "needs" ? "No saved answer" : "Empty"),
      open ? editor(r) : null);
  }
  function fieldsPart() {
    const rows = state.rows || [];
    if (!rows.length) return null;
    return ORDER.map((kind) => {
      const list = rows.filter((r) => r.status === kind);
      if (!list.length) return null;
      return el("details", { open: kind !== "skipped" || list.length <= 3 ? true : null, "data-group": kind },
        el("summary", {}, status(kind, `${STATUS[kind][1]} (${list.length})`)), el("ul", {}, list.slice(0, 80).map(fieldRow)));
    });
  }
  function learnedPart() {
    const items = state.learned || [];
    if (!items.length) return null;
    const what = (x) => (x.scope === "company" ? `Saved for ${x.company} only` : x.scope === "snippet" ? "Saved with this application" : "Saved for next time");
    return el("div", { class: "learned", role: "status" }, items.slice(-6).map((x) => el("div", { class: "btns" },
      status("filled", what(x)), el("span", { class: "v" }, `${x.label} = ${String(x.answer).slice(0, 60)}${String(x.answer).length > 60 ? "…" : ""}`),
      x.undone ? el("span", { class: "small" }, "Undone") : btn("Undo", () => actions.undo(x), "link", "Forget this answer (the earlier one comes back)"))));
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
      body.scrollTop = top;
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
    const body = el("div", { class: "body" },
      s.update ? el("div", { class: "warn", role: "status" }, s.update) : null,
      s.health && !s.health.ok ? el("div", {}, status("bad", "Job Hunt Agent is not answering"), el("div", { class: "small" }, s.health.message)) : null,
      s.error ? el("div", {}, status("bad", s.error), " ", btn("Settings", () => actions.settings(), "link")) : null,
      el("div", { class: "facts" }, jobPart(), resumePart(), addressPart(), slot("sponsorship")),
      s.captcha ? el("div", { class: "msg" }, "Solve the check on the page, then click Autofill") : null,
      s.message ? el("div", { class: "msg", role: "status" }, s.message) : null,
      btn(s.busy ? "Filling…" : "Autofill", () => actions.autofill(), "primary"),
      el("label", { class: "toggle" }, el("input", { type: "checkbox", checked: s.auto, onchange: (e) => actions.auto(e.target.checked) }), "Auto-fill each new page"),
      c ? el("div", { class: "counts" }, `Filled ${c.filled} · Needs you ${c.needs} · Skipped ${c.skipped}`) : null,
      c && c.notes ? el("div", { class: "small" }, c.notes) : null,
      learnedPart(), fieldsPart(),
      s.applied ? status("filled", s.applied) : null,
      el("div", { class: "foot" }, btn("Mark applied", () => actions.applied(), "btn", "Same as Mark applied in Job Hunt Agent"),
        btn("Send page snapshot", () => actions.snapshot(), "btn", "Sends this form's HTML (no passwords) so a failing page can be fixed"),
        s.openNeeds ? el("span", { class: "small" }, `${s.openNeeds} question(s) wait in Job Hunt Agent › Answers`) : null));
    const kept = keepPlace();
    box.replaceChildren(headPart(), body);
    kept(body);
    const needs = (s.rows || []).filter((r) => r.status === "needs").length || (c && c.needs) || 0;
    tab.replaceChildren(el("span", { class: "tv" }, "Autofill"), needs ? el("span", { class: "n", title: `${needs} need you` }, `${needs} ▲`) : null);
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
    hide() { wanted = false; push(false); if (host) host.remove(); },
    toggle() { if (host && host.isConnected) JH.panel.hide(); else JH.panel.show(); },
    shown: () => Boolean(host && host.isConnected),
    layout: () => ({ ...layout, rect: box && !box.hidden ? box.getBoundingClientRect().toJSON() : null }),
    set(patch) { state = { ...state, ...patch }; render(); },
    get: () => state,
    on(handlers) { actions = handlers; },
  };
})();

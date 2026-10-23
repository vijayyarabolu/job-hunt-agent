// Boot: profile, theme, navigation, status button, Settings, live updates.
import { $, $$, B, JOB_TABS, S, TABLE_TABS, TABS, api, closePop, get, h, menu, openPop, opt, phone, placeholder, sectionOf, store, tabName, titled, toast } from "./core.js";
import { applyProfileFilters, dropDupes, initFilters } from "./filters.js";
import { checkNew, keepList, loadFeed, tickAges } from "./feed.js";
import { closeDetail, openOnReload, wireSwipe } from "./detail.js";
import { loadFollowups, refreshFollowups as refreshFollowupCount, setTrackerReload } from "./editor.js";
import { initKeys } from "./keys.js";

const HEALTH_MS = 60000;
const GOOGLE_MS = 60000;
const AGES_MS = 60000;
const AFTER_FIRST_PAINT_MS = 1500;
let live = null;
let backTab = "recommended";

function setTheme(t) {
  if (t) document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
  store.set("jh.theme", t || "");
}
const isDark = () => document.documentElement.dataset.theme === "dark" ||
  (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
const toggleTheme = () => setTheme(isDark() ? "light" : "dark");

function paintTabs() {
  const view = S.tab === "settings" ? null : S.tab;
  for (const b of $$("#tabs [data-tab]")) {
    b.setAttribute("aria-selected", String(b.dataset.tab === view));
    b.tabIndex = b.dataset.tab === (view || "recommended") ? 0 : -1;
  }
  const [, tabs] = sectionOf(view);
  for (const b of $$("#btabs [data-tab]")) {
    const on = view ? b.dataset.tab === tabs[0] : b.dataset.tab === "more";
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
  $("#subtabs").replaceChildren(...(view && tabs.length > 1 ? tabs.map((t) => B({ class: "chip", "data-sub": t, "aria-pressed": String(t === view),
    title: $(`#tabs [data-tab="${t}"]`)?.title, onclick: () => switchTab(t) }, tabName(t))) : []));
  $("#view-title").textContent = view ? tabName(view) : "Settings";
  document.body.dataset.view = !view ? "settings" : JOB_TABS.includes(view) ? "jobs" : TABLE_TABS.includes(view) ? "table" : view;
}

function route() {
  const jobs = JOB_TABS.includes(S.tab);
  const noFilters = S.tab === "resumes" || S.tab === "applied";
  $("#pane-list").hidden = !jobs;
  $("#filters").hidden = !jobs || noFilters;           // GOAL33 §3d: a page with no filters has no empty band
  $("#view").hidden = jobs;
  paintTabs();
  if (jobs) return loadFeed();
  closeDetail();
  if (TABLE_TABS.includes(S.tab)) return lazyPage("./tracker.js", (m) => m.loadTracker());   // lazy, startup JS cap
  if (S.tab === "answers") return lazyPage("./answers.js", (m) => m.loadAnswers());   // lazy
  return S.tab === "followups" ? loadFollowups() : lazyPage("./stats.js", (m) => m.loadStats());
}

// a placeholder while a lazy page's module first loads, then drawn at once
const mods = {};
const quietTracker = () => mods["./tracker.js"]?.loadTracker({ quiet: true });
function lazyPage(path, draw) {
  if (mods[path]) return Promise.resolve(draw(mods[path]));
  $("#view").replaceChildren(placeholder());
  return import(path).then((m) => draw(mods[path] = m));
}

function switchTab(tab) {
  if (!TABS.includes(tab)) return;
  closePop({ refocus: false });
  document.body.classList.remove("panel-open");
  if (S.tab !== "settings") S.place[S.tab] = S.tab === "resumes" ? { top: 0 }    // numbered cards, from #1
    : { uid: S.items[S.cur]?.uid, top: $("#list-wrap").scrollTop };
  if (tab !== S.tab) dropDupes();      // "Duplicates only" is per list
  if (JOB_TABS.includes(S.tab)) keepList();
  // no view transition (old and new page showed over each other): its last data at once, a 140 ms fade-in
  S.tab = tab;
  S.cur = 0;
  history.replaceState(null, "", `#${tab}`);
  route();
  const pane = JOB_TABS.includes(tab) ? $("#pane-list") : $("#view");
  pane.classList.remove("enter");
  requestAnimationFrame(() => pane.classList.add("enter"));
}

// Settings is a page (side menu of sections), not a dialog; Back, Esc and the side menu leave it
function openSettings(section) {
  if (S.tab !== "settings") {
    backTab = S.tab;
    S.place[S.tab] = { uid: S.items[S.cur]?.uid, top: $("#list-wrap").scrollTop };
    history.pushState({ settings: 1 }, "", "#settings");
  }
  closePop({ refocus: false });
  document.body.classList.remove("panel-open");
  closeDetail();
  S.tab = "settings";
  $("#pane-list").hidden = true;
  $("#filters").hidden = true;
  $("#view").hidden = false;
  paintTabs();
  return lazyPage("./settings.js", (m) => m.showSettings({ section, close: closeSettings }))
    .catch(() => toast("Settings could not load: reload the page", { error: true }));
}
function closeSettings() {
  if (S.tab !== "settings") return;
  if (history.state?.settings) history.back(); else switchTab(backTab);
}
addEventListener("popstate", () => { if (S.tab === "settings" && !history.state?.settings) switchTab(backTab); });

const answerCount = () => { const n = $('#tabs [data-tab="answers"] .n')?.textContent; return n ? ` (${n} need you)` : ""; };
const refreshAnswers = () => import("./answers.js").then((m) => m.refreshBadge()).catch(() => {});

function moreMenu(anchor) {
  const key = (k) => (phone.matches ? "" : ` (${k})`);
  openPop(menu(
    titled(opt("Settings", () => openSettings()), `Your answers, job filters, daily goal, accounts${key(",")}`),
    titled(opt(`Answers${answerCount()}`, () => switchTab("answers")), "What the Autofill extension fills in, and the questions it needs you for"),
    titled(opt(isDark() ? "Light theme" : "Dark theme", toggleTheme), `Light or dark theme${key("t")}`),
    phone.matches ? null : titled(opt("Keyboard shortcuts", () => $("#help").showModal()), "Every keyboard shortcut (?)"),   // W15: no keys on a phone
    h("hr"), titled(opt("Setup and keys", () => openSettings("setup")), "Accounts and API keys: connected, missing or failing")),
  anchor, "More");
}

function wireTabs() {
  $("#tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-tab]"); if (b) switchTab(b.dataset.tab); });
  $("#tabs").addEventListener("keydown", (e) => {
    const step = { ArrowDown: 1, ArrowUp: -1, ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const tabs = $$("#tabs [data-tab]");
    const next = tabs[(tabs.indexOf(e.target) + step + tabs.length) % tabs.length];
    next.focus();
    switchTab(next.dataset.tab);
  });
  $("#btabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (!b) return;
    if (b.dataset.tab === "more") moreMenu(b); else switchTab(b.dataset.tab);
  });
  $("#settings-btn").addEventListener("click", () => openSettings());
  $("#keys-btn").addEventListener("click", () => $("#help").showModal());
}

// the person switch is always on screen, one click
function paintWho() {
  const p = S.profiles.find((x) => x.id === S.profile);
  $("#rg-link").onclick = (e) => { e.preventDefault(); openSettings("setup"); };
  $("#who").replaceChildren(h("span", "who-name sr", p ? p.name : ""), ...S.profiles.map((x) => B({ "data-person": x.id,
    "data-initial": x.name.slice(0, 1).toUpperCase(), "aria-pressed": String(x.id === S.profile), "aria-label": x.name,
    title: `Show ${x.name}'s jobs${x.has_baseline ? "" : " (no resume yet)"}` }, h("span", "who-label", x.name.split(/\s+/)[0]))));
}

function profileMenu(anchor) {
  openPop(menu(h("p", "pop-h", "Whose jobs"),
    S.profiles.map((p) => titled(opt(p.has_baseline ? p.name : `${p.name} (no resume yet)`, () => setProfile(p.id), p.id === S.profile),
      `Show ${p.name}'s jobs`)),
    h("hr"), titled(opt("Settings…", () => openSettings()), "Your answers, job filters, daily goal, accounts")),
  anchor, "Switch profile");
}

function setProfile(pid) {
  if (pid === S.profile || !S.profiles.some((p) => p.id === pid)) return;
  S.profile = pid;
  S.cur = 0;
  store.set("jh.profile", pid);
  history.replaceState(history.state, "", `#${S.tab}`);
  paintWho();
  paintRemembered();
  applyProfileFilters();
  closeDetail();
  mods["./tracker.js"]?.resetTracker();
  live?.restartLive();
  loadGoogle().then(() => (S.tab === "settings" ? openSettings() : route()));
  loadHealth();
  refreshFollowupCount();
  refreshAnswers();
  S.g8?.profile();
}

// GOAL33 §3d (user 2026-10-22): a reload stays on the page the user was on (and the job open there) with that page's
// default filters, sort and scroll; a new open still starts on Recommended (resumegen GOAL3 §7).
function applyDeepLink() {
  const [tab, pid] = decodeURIComponent(location.hash.slice(1)).split(":");
  const how = performance.getEntriesByType("navigation")[0]?.type;
  if (TABS.includes(tab) && (how === "back_forward" || how === "reload")) S.tab = tab;
  else if (tab && tab !== S.tab) history.replaceState(null, "", `#${S.tab}`);
  if (pid && S.profiles.some((p) => p.id === pid)) { S.profile = pid; store.set("jh.profile", pid); }
  const open = how === "reload" ? openOnReload() : null;
  if (open?.tab === S.tab && JOB_TABS.includes(S.tab)) S.place[S.tab] = { uid: open.uid, top: 0 };
}

// one status button (dot + a word) with a popover; a banner only when the user must act
const driftKey = () => `jh.drift.${S.profile}`;
const driftSig = (drift) => drift.replace(/\d+(\.\d+)?/g, "#");
const healthKey = (pid = S.profile) => `jh.health.${pid}`;
const googleKey = (pid = S.profile) => `jh.google.${pid}`;
const ST = { health: null, google: null };
const RANK = { bad: 3, warn: 2, info: 1, ok: 0 };
function paintRemembered(pid = S.profile) {
  ST.health = store.json(healthKey(pid), null);
  ST.google = store.json(googleKey(pid), null);
  paintStatus();
}
function notices() {
  const x = ST.health, g = ST.google, out = [];
  if (x) {
    const drift = x.drift?.length ? `Data drift: ${x.drift.join("; ")}` : "";
    if (x.stale) out.push({ level: "bad", text: `Job boards not checked for ${x.last_ok_fetch_min ?? "?"} min: new jobs may be missing` });
    else if (x.last_ok_fetch_min != null) out.push({ level: "ok", text: `Job boards checked ${x.last_ok_fetch_min} min ago` });
    if (x.failing?.length) out.push({ level: "warn", text: `${x.failing.length} job board${x.failing.length === 1 ? "" : "s"} failing 3+ times`, sub: "The other boards still bring jobs in" });
    if (drift && store.get(driftKey()) !== driftSig(drift)) out.push({ level: "warn", text: drift, hide: () => { store.set(driftKey(), driftSig(drift)); paintStatus(); } });
    const n = x.pending_fits?.[S.profile];
    if (n) out.push({ level: "info", text: `${n} job${n === 1 ? "" : "s"} still scoring…`, sub: "They show up in Recommended when done" });
  }
  if (g?.alert) out.push({ level: "bad", text: g.alert });
  if (g?.overnight) out.push({ level: /need/.test(g.overnight) ? "warn" : "info", text: g.overnight, go: ["Open the Tracker's email box", () => switchTab("tracker")] });
  if (g?.sync_note) out.push({ level: "warn", text: g.sync_note });
  else if (g?.sync && (g.sync.linked || []).includes(S.profile) && !g.alert) out.push({ level: "ok", text: "Google Sheet in sync" });
  if (S.rg && !S.rg.on && S.rg.message) out.push({ level: "warn", text: S.rg.message });
  return out;
}
function paintStatus() {
  const list = notices();
  const worst = list.reduce((m, n) => Math.max(m, RANK[n.level]), 0);
  const told = list.filter((n) => RANK[n.level] >= RANK.warn);
  const btn = $("#status");
  btn.querySelector(".dot").className = `dot${worst === 3 ? " bad" : worst === 2 ? " warn" : ST.health ? "" : " off"}`;
  btn.querySelector(".status-text").textContent = worst === 3 ? "Needs you" : told.length ? `${told.length} notice${told.length === 1 ? "" : "s"}` : "All good";
  btn.title = `Status: ${list.filter((n) => n.level !== "ok").map((n) => n.text).join(" · ") || "all good"}`;
  btn.setAttribute("aria-label", btn.title);
  const must = list.filter((n) => n.level === "bad");
  $("#banner").textContent = must.find((n) => n.text !== ST.google?.alert)?.text || "";
  $("#banner").hidden = !$("#banner").textContent;
  $("#gbanner").textContent = ST.google?.alert || "";
  $("#gbanner").hidden = !ST.google?.alert;
  if (btn.getAttribute("aria-expanded") === "true") statusPop(btn, true);
}
function statusPop(anchor, redraw = false) {
  const lines = notices().map((n) => h("div", "st-line", h("span", { class: `dot${n.level === "ok" ? "" : n.level === "info" ? " off" : ` ${n.level}`}`, "aria-hidden": "true" }),
    h("b", {}, n.text), n.sub ? h("span", "muted", n.sub) : null,
    n.go ? B({ class: "lnk", onclick: n.go[1] }, n.go[0]) : null,
    n.hide ? B({ class: "lnk", title: "Hide this notice until it changes", onclick: n.hide }, "Hide until it changes") : null));
  const body = h("div", "st-pop", h("h3", {}, "Status"), lines.length ? lines : h("p", "muted small", "Loading…"),
    h("div", "st-line", h("span"), B({ class: "lnk", onclick: () => openSettings("accounts") }, "Accounts and sign-in in Settings")));
  if (redraw && !$("#pop").hidden) { $("#pop").replaceChildren(body); return; }
  openPop(body, anchor, "Status");
}
async function loadHealth() {
  if (document.hidden) return;
  const pid = S.profile;
  try {
    const x = await get("/api/health");
    if (pid !== S.profile) return; // switched person while it loaded
    store.set(healthKey(), JSON.stringify(x));
    ST.health = x;
    paintStatus();
  } catch { /* health is best effort */ }
}
async function loadGoogle() {
  try {
    const pid = S.profile;
    const g = await get("/api/google");
    if (pid !== S.profile) return;
    S.google = g;
    S.sheetUrl = g.sheet_url;
    ST.google = { alert: g.alert, sync_note: g.sync_note, overnight: g.overnight, sync: g.sync };
    store.set(googleKey(), JSON.stringify(ST.google));
    paintStatus();
  } catch { /* Google is optional */ }
}

function onEdit() {
  refreshFollowupCount();
  refreshAnswers();
  if (TABLE_TABS.includes(S.tab) && !$("#view .editing, #view .g-ed")) quietTracker();
  if (S.tab === "followups") loadFollowups();
}

async function init() {
  history.scrollRestoration = "manual";
  setTheme(store.get("jh.theme") || null);
  $("#theme").addEventListener("click", toggleTheme);
  $("#who").addEventListener("click", (e) => { const b = e.target.closest("[data-person]"); if (b) setProfile(b.dataset.person); else profileMenu($("#who")); });
  $("#status").addEventListener("click", (e) => statusPop(e.currentTarget));
  paintRemembered(decodeURIComponent(location.hash.slice(1)).split(":")[1] || S.profile); // before any await: no shift
  applyProfileFilters();
  S.profiles = await api("/api/profiles").catch(() => []);
  applyDeepLink();
  if (S.profiles.length && !S.profiles.some((p) => p.id === S.profile)) {
    S.profile = (S.profiles.find((p) => p.default) || S.profiles[0]).id;
  }
  paintWho();
  paintRemembered();
  initFilters(() => { S.cur = 0; loadFeed(); });
  applyProfileFilters();
  wireTabs();
  wireSwipe();
  setTrackerReload(() => { if (TABLE_TABS.includes(S.tab)) quietTracker(); });
  initKeys({ switchTab, toggleTheme, setProfile, profileMenu, openSettings, closeSettings });
  S.switchTab = switchTab;
  S.openSettings = openSettings;
  S.paintStatus = paintStatus;
  if (!S.profiles.length) {                                     // first run: Settings > Profiles to add someone
    openSettings("profiles");
    return;
  }
  phone.addEventListener("change", () => { if (TABLE_TABS.includes(S.tab)) quietTracker(); });
  const google = loadGoogle();
  if (!JOB_TABS.includes(S.tab)) await google;
  if (location.hash.startsWith("#settings")) openSettings(location.hash.split("/")[1] || "setup");   // back from Google
  else route();
  loadHealth();
  setTimeout(() => { refreshFollowupCount(); refreshAnswers(); import("./rg.js").then((m) => m.init8())
    .catch(() => toast("Resumes and “Did you apply?” could not load: reload the page", { error: true }));
  import("./tip.js").then((m) => m.initTips()).catch(() => {});
  import("./edges.js").then((m) => m.initEdges()).catch(() => {});
  import("./live.js").then((m) => { live = m; m.startLive({ onRun: () => { checkNew(); loadHealth(); }, onEdit, onRg: () => S.g8?.poll() }); }).catch(() => {}); },
    AFTER_FIRST_PAINT_MS);
  setInterval(loadHealth, HEALTH_MS);
  setInterval(tickAges, AGES_MS);
  setInterval(() => { if (!document.hidden) loadGoogle(); }, GOOGLE_MS);
  if (isSecureContext && navigator.serviceWorker) navigator.serviceWorker.register("/sw.js").catch(() => {});
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
else init();

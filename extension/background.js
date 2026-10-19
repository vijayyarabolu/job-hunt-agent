// Job Hunt Agent Autofill: the service worker. It is the only part that talks to Job Hunt Agent (address + LAN token from the
// settings page), relays "fill this page" to every frame of the tab (a Greenhouse or iCIMS form inside an iframe),
// and keeps each tab's autofill session (the matched job, "auto-fill each new page") while the tab lives.
// The job-site account (email, password) never leaves chrome.storage.local: a request carrying the password is refused.
// what the user fills by hand is staged here per tab (chrome.storage.local, so a closed tab or even a browser
// restart loses nothing) and sent to Job Hunt Agent when the user leaves the step, the page changes or the tab closes.
"use strict";

const TIMEOUT_MS = 30000;
const SESSION_PREFIX = "tab:";
const MIN_SECRET = 4;
const CHUNK = 0x8000;
const LEARN_PREFIX = "learn:";
const LAST_PREFIX = "learned:";
const LAST_MS = 2 * 60 * 1000;
const MAX_STAGED = 40;

async function settings() {
  const got = await chrome.storage.local.get(["base", "token", "profile", "account"]);
  return { base: String(got.base || "").replace(/\/+$/, ""), token: got.token || "", profile: got.profile || "",
           password: (got.account && got.account.password) || "" };
}

// raw, HTML-, JSON- and URL-escaped: a password with & or " is still caught inside a snapshot's HTML
function secretForms(pw) {
  const html = pw.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const json = (s) => JSON.stringify(s).slice(1, -1);
  return [...new Set([pw, html, html.replace(/'/g, "&#39;"), json(pw), json(html), encodeURIComponent(pw)])];
}

function carriesPassword(text, password) {
  if (!password || password.length < MIN_SECRET) return false;
  const body = String(text || "");
  return secretForms(password).some((form) => body.includes(form));
}

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let out = "";
  for (let i = 0; i < bytes.length; i += CHUNK) out += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  return btoa(out);
}

async function api({ method = "GET", path, query = {}, body = null, binary = false }) {
  const s = await settings();
  if (!s.base) return { ok: false, error: "Set the Job Hunt Agent address in the extension's settings first" };
  if (!String(path || "").startsWith("/api/autofill/")) return { ok: false, error: "not an autofill call" };
  const url = new URL(s.base + path);
  for (const [k, v] of Object.entries({ profile: s.profile, ...query })) if (v != null && v !== "") url.searchParams.set(k, v);
  const payload = body == null ? null : JSON.stringify({ profile: s.profile, ...body });
  if (carriesPassword(url.href, s.password) || carriesPassword(payload, s.password)) {
    return { ok: false, error: "refused: this request would have carried your job-site password" };
  }
  const headers = { "X-Jobhunt": "1", "X-Jobhunt-Token": s.token, "X-Jobhunt-Version": chrome.runtime.getManifest().version };
  if (payload != null) headers["Content-Type"] = "application/json";
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, { method, headers, body: payload, signal: ctrl.signal, credentials: "omit" });
    if (binary && res.ok) {
      const name = /filename\*=UTF-8''([^;]+)/.exec(res.headers.get("Content-Disposition") || "");
      return { ok: true, data: { b64: toBase64(await res.arrayBuffer()), type: res.headers.get("Content-Type") || "",
                                 name: name ? decodeURIComponent(name[1]) : "resume.pdf" } };
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, status: res.status, error: data.error || `Job Hunt Agent answered ${res.status}` };
    return { ok: true, data };
  } catch (err) {
    return { ok: false, error: err.name === "AbortError" ? "Job Hunt Agent did not answer in time"
                                                           : "Job Hunt Agent is not reachable: check the address in settings" };
  } finally {
    clearTimeout(timer);
  }
}

const sessionKey = (tabId) => SESSION_PREFIX + tabId;

async function getSession(tabId) {
  const got = await chrome.storage.session.get(sessionKey(tabId));
  return got[sessionKey(tabId)] || null;
}

async function setSession(tabId, patch) {
  const next = { ...(await getSession(tabId)), ...patch, at: Date.now() };
  await chrome.storage.session.set({ [sessionKey(tabId)]: next });
  return next;
}

// ---- hand answers, staged per tab and step, flushed to Job Hunt Agent -----------------------------------------
const chains = new Map();
async function stageLearn(tabId, key, batch) {
  const k = LEARN_PREFIX + tabId;
  const got = await chrome.storage.local.get(k);
  const staged = { ...(got[k] || {}), [String(key).slice(0, 600)]: { ...batch, at: Date.now() } };
  const keep = Object.entries(staged).sort((a, b) => b[1].at - a[1].at).slice(0, MAX_STAGED);
  await chrome.storage.local.set({ [k]: Object.fromEntries(keep) });
}

async function flushNow(tabId, notify) {
  const k = LEARN_PREFIX + tabId;
  const got = await chrome.storage.local.get(k);
  const staged = got[k] || {};
  if (!Object.keys(staged).length) return [];
  await chrome.storage.local.remove(k);                 // taken: a second flush never posts it twice
  const entries = [];
  const unsent = {};
  for (const [key, batch] of Object.entries(staged)) {
    const { at, ...body } = batch;
    const res = await api({ method: "POST", path: "/api/autofill/learn", body });
    if (res.ok) entries.push(...((res.data && res.data.learned) || []));
    else if (!res.status) unsent[key] = batch;          // Job Hunt Agent unreachable: kept for the next flush
  }
  if (Object.keys(unsent).length) {
    const now = await chrome.storage.local.get(k);
    await chrome.storage.local.set({ [k]: { ...unsent, ...(now[k] || {}) } });
  }
  if (entries.length) {
    await chrome.storage.session.set({ [LAST_PREFIX + tabId]: { entries, at: Date.now() } });
    if (notify) chrome.tabs.sendMessage(tabId, { type: "learned", entries }, { frameId: 0 }).catch(() => {});
  }
  return entries;
}

function flushLearn(tabId, notify = true) {             // one flush at a time per tab
  const next = (chains.get(tabId) || Promise.resolve()).then(() => flushNow(tabId, notify)).catch(() => []);
  chains.set(tabId, next);
  return next;
}

async function lastLearned(tabId) {
  const k = LAST_PREFIX + tabId;
  const got = (await chrome.storage.session.get(k))[k];
  await chrome.storage.session.remove(k);
  return got && Date.now() - got.at < LAST_MS ? got.entries : [];
}

async function flushLeftovers() {                       // batches of tabs that no longer exist (browser restarted)
  const all = await chrome.storage.local.get(null);
  const open = new Set((await chrome.tabs.query({})).map((t) => String(t.id)));
  for (const key of Object.keys(all)) {
    if (key.startsWith(LEARN_PREFIX) && !open.has(key.slice(LEARN_PREFIX.length))) flushLearn(key.slice(LEARN_PREFIX.length), false);
  }
}
chrome.runtime.onStartup.addListener(flushLeftovers);
chrome.runtime.onInstalled.addListener(flushLeftovers);

chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.storage.session.remove(sessionKey(tabId));
  flushLearn(tabId, false);
});
chrome.tabs.onUpdated.addListener((tabId, change) => { if (change.url) flushLearn(tabId); });   // a new page or step address

chrome.action.onClicked.addListener((tab) => {
  if (tab.id != null) chrome.tabs.sendMessage(tab.id, { type: "togglePanel" }, { frameId: 0 }).catch(() => {});
});

async function handle(msg, sender) {
  const tabId = sender.tab && sender.tab.id;
  switch (msg && msg.type) {
    case "api": return api(msg);
    case "session:get": {                                // + the tab's own page address, for frames too
      const data = tabId == null ? null : await getSession(tabId);
      return { ok: true, data: data && { ...data, tabUrl: sender.tab.url || "" } };
    }
    case "session:set": return { ok: true, data: tabId == null ? null : await setSession(tabId, msg.patch || {}) };
    case "broadcast":                                    // top frame -> every frame of the tab (fill, highlight)
      if (tabId != null) chrome.tabs.sendMessage(tabId, msg.message).catch(() => {});
      return { ok: true };
    case "toTop":                                        // a frame's result -> the panel in the top frame
      if (tabId != null) {
        chrome.tabs.sendMessage(tabId, { ...msg.message, frameUrl: sender.url }, { frameId: 0 }).catch(() => {});
      }
      return { ok: true };
    case "openOptions": chrome.runtime.openOptionsPage(); return { ok: true };
    case "learn:stage": if (tabId != null && msg.batch) await stageLearn(tabId, msg.key || "", msg.batch); return { ok: true };
    case "learn:flush": if (tabId != null) flushLearn(tabId); return { ok: true };
    case "learn:last": return { ok: true, data: tabId == null ? [] : await lastLearned(tabId) };
    case "viewResume": {                                 // the tailored PDF, in a tab of its own (extension page)
      const q = new URLSearchParams({ uid: msg.uid || "", app: msg.app || "" });
      await chrome.tabs.create({ url: chrome.runtime.getURL(`resume.html?${q}`), index: sender.tab ? sender.tab.index + 1 : undefined });
      return { ok: true };
    }
    case "queue:open": return openQueue(msg.items, sender);
    default: return { ok: false, error: "unknown message" };
  }
}

// "Open next 5" on the Job Hunt Agent page (only that page: its address is the one in Settings): each apply page
// in a new tab that already knows its job, so the panel and auto-fill start without a pick
const QUEUE_MAX = 10;
async function openQueue(items, sender) {
  const { base } = await chrome.storage.local.get("base");
  let own = "";
  try { own = new URL(base).origin; } catch { own = ""; }
  if (!own || !sender.url || new URL(sender.url).origin !== own) return { ok: false, error: "only the Job Hunt Agent page can open its queue" };
  const list = (Array.isArray(items) ? items : []).filter((x) => x && /^https?:\/\//.test(String(x.url || ""))).slice(0, QUEUE_MAX);
  let at = sender.tab ? sender.tab.index + 1 : undefined;
  for (const x of list) {
    const tab = await chrome.tabs.create({ url: String(x.url), active: false, index: at });
    if (at != null) at += 1;
    await setSession(tab.id, { job: { uid: x.uid || null, app: x.app || null, company: String(x.company || ""), title: String(x.title || "") },
      jobAt: Date.now() });
  }
  return { ok: true, data: { opened: list.length } };
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  handle(msg, sender).then(reply, (err) => reply({ ok: false, error: String((err && err.message) || err) }));
  return true;                                           // answer asynchronously
});

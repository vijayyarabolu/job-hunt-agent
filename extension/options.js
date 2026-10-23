// Job Hunt Agent Autofill settings: everything is kept in chrome.storage.local on this laptop only.
"use strict";
const $ = (id) => document.getElementById(id);
const MASK_KEEP = 2;

function masked(account) {
  if (!account || !account.email) return "nothing yet";
  const pw = account.password || "";
  return `${account.email} · password ${pw ? "•".repeat(Math.max(8, pw.length - MASK_KEEP)) : "not set"}`;
}

async function load() {
  const s = await chrome.storage.local.get(["base", "token", "profile", "account", "auto", "suggest"]);
  $("base").value = s.base || "http://127.0.0.1:8765";
  $("token").value = s.token || "";
  await loadProfiles(s.profile);
  $("email").value = (s.account && s.account.email) || "";
  $("password").value = (s.account && s.account.password) || "";
  $("auto").checked = s.auto !== false;
  $("suggest").checked = s.suggest !== false;
  $("masked").textContent = masked(s.account);
}

// the app's own profiles (Settings > Profiles), one radio each; the saved one stays picked
async function loadProfiles(saved) {
  const res = await chrome.runtime.sendMessage({ type: "api", path: "/api/profiles" }).catch(() => null);
  const list = res && res.ok && Array.isArray(res.data) ? res.data : [];
  if (!list.length) return;
  const pick = saved && list.some((p) => p.id === saved) ? saved : (list.find((p) => p.default) || list[0]).id;
  $("profiles").replaceChildren(...list.map((p) => {
    const label = document.createElement("label");
    const input = Object.assign(document.createElement("input"), { type: "radio", name: "profile", value: p.id, checked: p.id === pick });
    label.append(input, ` ${p.name}`);
    return label;
  }));
  $("profiles-hint").textContent = "";
}

function say(el, text, good) {
  el.textContent = text;
  el.className = good ? "ok" : "bad";
}

async function save(e) {
  e.preventDefault();
  let base = $("base").value.trim().replace(/\/+$/, "");
  try { base = new URL(base).origin; } catch { return say($("saved"), "The Job Hunt Agent address is not a web address", false); }
  const account = { email: $("email").value.trim(), password: $("password").value };
  if (account.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(account.email)) return say($("saved"), "That email does not look right", false);
  const profile = document.querySelector("input[name=profile]:checked")?.value || (await chrome.storage.local.get("profile")).profile || "";
  await chrome.storage.local.set({ base, token: $("token").value.trim(), profile, account, auto: $("auto").checked, suggest: $("suggest").checked });
  $("masked").textContent = masked(account);
  say($("saved"), "Saved", true);
}

async function test() {
  say($("test-out"), "Checking…", true);
  await save(new Event("submit"));
  await loadProfiles((await chrome.storage.local.get("profile")).profile);
  await save(new Event("submit"));
  const res = await chrome.runtime.sendMessage({ type: "api", path: "/api/autofill/ping" });
  if (res && res.ok) say($("test-out"), `Connected as ${res.data.profile} · ${res.data.open} question(s) need you`, true);
  else say($("test-out"), (res && res.error) || "No answer", false);
}

$("form").addEventListener("submit", save);
$("test").addEventListener("click", test);
$("show").addEventListener("click", () => {
  const on = $("password").type === "password";
  $("password").type = on ? "text" : "password";
  $("show").textContent = on ? "Hide" : "Show";
  $("show").setAttribute("aria-pressed", String(on));
});
load();

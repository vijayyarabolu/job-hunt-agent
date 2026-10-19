// Settings as a page with a side menu. Every field saves on its own (change / Enter) and says "Saved ✓";
// Back, Esc or the side menu leave it. Lazy: loaded on first open.
import { $, B, S, api, copyText, extLink, get, h, muted, q } from "./core.js";
import { mailMount } from "./tracker.js";

const SECTIONS = [["setup", "Setup", "Accounts and keys: what is connected, missing or failing, with a Test for each"],
  ["profiles", "Profiles", "Add a person, upload a resume"],
  ["answers", "Profile & answers", "The answers job forms ask (salary, start date, links), copied with one click"],
  ["filters", "Job filters", "Years of experience, minimum match, past employers"], ["goal", "Daily goal", "How many applications a day you aim for"],
  ["accounts", "Accounts", "This profile's Google Sheet and job-application email"],
  ["autofill", "Autofill extension", "Fill job applications with your tailored resume: get it, install it"],
  ["about", "About & help", "What the numbers mean, keys, where things are"]];
let shown = "setup";
const NEEDS_PROFILE = new Set(["answers", "filters", "goal", "accounts", "autofill"]);
let close = () => {};

function saver(input, send, { after = null, valid = null } = {}) {
  const state = h("span", { class: "saved-state", role: "status" });
  const value = () => (input.type === "checkbox" ? String(input.checked) : input.value);
  input.dataset.saved = value();
  const run = async () => {
    if (value() === input.dataset.saved) return;
    const bad = valid?.() || (input.validity && !input.validity.valid ? `Must be a whole number from ${input.min} to ${input.max}` : "");
    state.classList.toggle("err", !!bad);
    if (bad) { state.textContent = bad; return; }
    state.textContent = "Saving…";
    try {
      const out = await send();
      input.dataset.saved = value();
      state.textContent = "Saved ✓";
      after?.(out);
    } catch (err) { state.classList.add("err"); state.textContent = `Not saved: ${err.message}`; }
  };
  input.addEventListener("change", run);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && input.tagName === "INPUT") { e.preventDefault(); run(); } });
  return state;
}
const num = (value, max, step = "1") => h("input", { type: "number", min: "0", max, step, inputmode: "numeric", value: value ?? "" });
function row(label, input, hint, state) {
  input.title = `${label}: saves as you leave the field (or press Enter)`;
  return h("label", "field set-field", h("span", {}, label), h("span", "set-in", input, state), hint ? h("span", "hint", hint) : null);
}
const refreshAnswers = () => import("./rq.js").then((m) => m.loadAnswers()).catch(() => {});

function answersPart(s) {
  const fields = s.answer_labels.map(([k, label]) => {
    const input = h("input", { type: "text", name: k, maxlength: "300", autocomplete: "off", value: s.answers?.[k] || "", "aria-label": label,
      title: `${label}: saves as you leave the field (or press Enter)` });
    const state = saver(input, () => api("/api/settings/apply", { answers: { [k]: input.value } }), { after: refreshAnswers });
    const copy = B({ class: "btn sm ghost", title: `Copy ${label}`, onclick: (e) => { e.preventDefault(); if (input.value) copyText(input.value, `Copied ${label}: ${input.value}`); } }, "Copy");
    return h("label", "field set-field", h("span", {}, label), h("span", "set-in", input, copy, state));
  });
  const hand = h("input", { type: "checkbox", title: "Apply ↗ also downloads the ready resume and copies the job's facts" });
  hand.checked = s.apply_in_hand !== false;
  const handState = saver(hand, () => api("/api/settings/apply", { in_hand: hand.checked }), { after: refreshAnswers });
  return [h("p", "hint", `${S.profiles.find((p) => p.id === S.profile)?.name || S.profile} · matching uses ${s.years ?? "?"} years (Job filters). ` +
    "These form answers show on every job (Answers on hand): click one there to copy it, or Edit to change it."),
  h("div", "set-group", fields),
  h("label", "check set-check", hand, h("span", {}, "Apply also downloads the ready resume and copies the job's facts (company, title, req id)"), handState)];
}

function filtersPart(s) {
  const cap = num(s.max_required_years, "30");
  const floor = num(s.feed_min_match ?? 0, "100", "5");
  const past = h("textarea", { rows: 4 }, (s.past_employers || []).join("\n"));
  return [row("Hide jobs that ask for more years than", cap, `You have ${s.years ?? "?"} years in your profile. Hidden jobs stay in Filters → More → Show too senior; nothing is deleted.`,
    saver(cap, () => api("/api/settings/years", { cap: cap.value }), { valid: () => (cap.value.trim() ? "" : "Must be a whole number from 0 to 30") })),
  row("Minimum match for Recommended (%)", floor, "0 shows every matched job; raise it to hide weaker matches (the Match filter still narrows further).",
    saver(floor, () => api("/api/settings/min_match", { min_match: floor.value }))),
  row("Past employers (one per line)", past, "Their jobs are hidden from your lists.",
    saver(past, () => api("/api/settings/past_employers", { names: past.value })))];
}

function goalPart(s) {
  const goal = num(s.daily_goal ?? 15, "100");
  return [row("Applications a day you aim for", goal, "Shown as “Applied today N · goal M” on Recommended and Tailored resumes; 0 hides it.",
    saver(goal, () => api("/api/settings/apply", { goal: goal.value || "0" }), { after: () => S.g8?.poll() }))];
}

function accountsPart() {
  const g = S.google || {};
  const linked = (g.sync?.linked || []).includes(S.profile);
  return [h("section", "set-card", h("h3", {}, "Google (Sheet and Drive)"),
    h("p", {}, linked ? (g.alert ? h("span", "bad", g.alert) : "Your Google Sheet is linked and syncing.") : "Your Google Sheet is not linked yet."),
    h("p", "hint", "Connect the app's Google account in Setup first. Then link your existing tracker sheet from a terminal: " +
      "job-hunt-agent google link --profile " + S.profile + " '<sheet URL>' (it shows a preview and changes nothing until you say yes)."),
    h("div", "row-btns", extLink(`/google/account?${q()}`, "Connect or check your Google account ↗", "btn", "Opens the Google account page in a new tab"),
      g.sheet_url ? extLink(g.sheet_url, "Open my Google Sheet ↗", "btn ghost", "Your tracker Sheet in a new tab") : null)),
  h("section", "set-card", h("h3", {}, "Gmail (overnight email check)"), muted("Reads your job emails once a night and updates the Tracker. Read only.", "hint"),
    mailMount(h("div", "mail-box"), "settings"))];
}

const info = (label, value, hint) => h("div", "field set-field", h("span", {}, label), h("span", "set-in", value), h("span", "hint", hint));

// the Chrome extension, as a zip from this app, with the four install steps and what to type in it
function autofillPart() {
  const tok = h("span", "mono", "…");
  const steps = ["Download the zip and unzip it (double-click). Keep the folder job-hunt-agent-autofill somewhere it stays, e.g. Documents.",
    "In Chrome open chrome://extensions and turn on Developer mode (top right).",
    "Click Load unpacked and choose the job-hunt-agent-autofill folder. Pin Autofill (puzzle icon › pin).",
    "Right-click the Autofill icon › Options: paste the app address and token below, pick your name, add the email and password you use on job sites (kept only in Chrome on this laptop), Save, then Test connection."];
  const card = h("section", "set-card", h("h3", {}, "Get the Autofill extension"),
    h("p", {}, "Fills job applications (Workday, Greenhouse, Lever and more) with your tailored resume and your saved answers. It never clicks Next or Submit, never solves a check, never guesses a legal or EEO answer."),
    h("div", "row-btns", h("a", { class: "btn primary", href: `/api/autofill/extension.zip?${q()}`, download: "job-hunt-agent-autofill.zip" }, "Get the Autofill extension"),
      B({ class: "btn ghost", onclick: () => S.switchTab("answers") }, "Open Answers")),
    h("ol", "set-list", steps.map((t) => h("li", {}, t))),
    info("App address", h("span", "mono", location.origin), "Paste this into the extension (it is this page's address)."),
    info("Token", tok, "Paste it into the extension once. Only this app knows it."));
  get("/api/autofill/setup").then((r) => {
    tok.textContent = r.token ? "" : "none needed on this computer";
    if (r.token) tok.append(B({ class: "btn sm", onclick: () => copyText(r.token, "Token copied") }, "Copy token"));
  }).catch((err) => { tok.textContent = `Could not read: ${err.message}`; });
  const health = h("div", {});                              // Health (loaded only when this section opens)
  import("./afhealth.js").then((m) => health.replaceWith(m.healthCard())).catch((err) => { health.textContent = `Health could not load: ${err.message}`; });
  return [card, health];
}

function aboutPart() {
  return [h("section", "set-card", h("h3", {}, "The two numbers on a job"),
    h("p", {}, h("b", {}, "Match %"), " (the ring): how well your best resume fits the job: experience, skills, industry and title."),
    h("p", {}, h("b", {}, "Priority"), " (0 to 100): Match × how fresh the posting is × company signals (posted pay, a named team, hiring momentum). 70+ means apply today."),
    extLink("/how/ats", "How the resume estimate is computed ↗", "lnk")),
  h("section", "set-card", h("h3", {}, "Keyboard"), h("p", {}, "Every key is listed in Keyboard shortcuts (?)."),
    B({ class: "btn", onclick: () => $("#help").showModal() }, "Keyboard shortcuts")),
  h("section", "set-card", h("h3", {}, "Where things are"),
    h("ul", "set-list", ["Jobs: Recommended (new matches), Saved, Archived (Not interested, rejected, ghosted).",
      "Tailored resumes: the queue, ready to apply, done today.", "Applications: Tracker (your Sheet), Applied, Interviewing, Follow-ups.",
      "Status (top right): job boards, scoring, overnight email, Google Sheet.", "Remove (×) takes a job out for good; Not interested keeps it in Archived."]
      .map((t) => h("li", {}, t))))];
}

async function paint(page, s) {
  const repaint = () => paint(page, s);
  const setup = () => import("./setup.js");
  const parts = { setup: () => setup().then((m) => m.setupPart(repaint)), profiles: () => setup().then((m) => m.profilesPart(repaint)),
    answers: () => answersPart(s), filters: () => filtersPart(s), goal: () => goalPart(s), accounts: accountsPart,
    autofill: autofillPart, about: aboutPart };
  const title = SECTIONS.find(([k]) => k === shown)[1];
  const body = !s && NEEDS_PROFILE.has(shown) ? [h("p", "hint", "Add a profile first (Settings › Profiles).")] : await parts[shown]();
  page.querySelector(".set-body").replaceChildren(h("h2", { id: "set-h" }, title), ...body.flat().filter((x) => x != null && x !== false));
  for (const b of page.querySelectorAll(".set-nav [data-sec]")) {
    if (b.dataset.sec === shown) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
}

export async function showSettings({ section, close: done }) {
  close = done;
  if (section && SECTIONS.some(([k]) => k === section)) shown = section;
  const view = $("#view");
  view.replaceChildren(h("div", "set-page", h("p", "muted pad", "Loading…")));
  let s;
  const pid = S.profile;
  try { s = pid ? await get("/api/settings") : null; } catch (err) { s = null; }
  if (S.tab !== "settings" || pid !== S.profile) return;
  const back = B({ class: "btn ghost set-back", title: "Back (Esc)", onclick: () => close() }, "← Back");
  const nav = h("nav", { class: "set-nav", "aria-label": "Settings sections" }, SECTIONS.map(([k, label, about]) =>
    B({ "data-sec": k, title: about, onclick: () => { shown = k; paint(page, s); page.querySelector(".set-body h2")?.focus?.(); } }, label)));
  const page = h("div", { class: "set-page", onkeydown: (e) => {
    if (e.key !== "Escape") return;
    e.stopPropagation();
    if (document.activeElement && page.contains(document.activeElement)) document.activeElement.blur();   // a typed value saves first
    close();
  } }, h("header", "set-top", back, h("h1", {}, "Settings")), nav, h("div", { class: "set-body", "aria-labelledby": "set-h" }));
  view.replaceChildren(page);
  await paint(page, s);
}

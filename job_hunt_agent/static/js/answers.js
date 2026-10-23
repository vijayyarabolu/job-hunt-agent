// /§7: the Answers page. What the Autofill extension fills into job forms, per person: "Needs you" first
// (each question once, its choices as buttons), then the addresses per city, the work history Workday's My Experience
// gets, and every saved answer, editable. what it learned from the user's own filling (Undo), company-only answers,
// saved long answers, fill stats (afmore.js); choice answers are dropdowns; education is a list of degrees; the start
// date setting is a weeks box. Lazy: loaded on first open.
import { $, B, S, api, fail, get, h, loadError, placeholder, refreshFailed, toast } from "./core.js";
import { companies, learned, snippets, stats, typoNote } from "./afmore.js";

const LEGAL_TIP = "Legal / government / EEO: filled only with the answer you save here, never guessed";
let data = null;

function css() {
  if (!document.querySelector('link[href="/answers.css"]')) document.head.append(h("link", { rel: "stylesheet", href: "/answers.css" }));
}

let drawn = "";      // GOAL31 §3: the answers last drawn; on the way back the same answers again redraw nothing

async function reload(back = false) {
  let got;
  try { got = await get("/api/autofill/answers"); } catch (err) {
    if (S.tab !== "answers") return;
    if (data) refreshFailed(err, () => reload()); else $("#view").replaceChildren(loadError(err, () => reload()));
    return;
  }
  const sig = `${S.profile}|${JSON.stringify(got)}`;
  data = { ...got, profile: S.profile };
  if (S.tab === "answers" && !(back && sig === drawn && $("#view .af"))) { render(); drawn = sig; }
  badge(data.open);
}

export function badge(n) {
  for (const el of document.querySelectorAll('[data-tab="answers"] .n')) el.textContent = n ? String(n) : "";
}

const post = async (path, body, ok) => {
  try { await api(path, body); if (ok) toast(ok); await reload(); } catch (err) { fail(err); }
};

function needCard(n) {
  const where = [n.companies.slice(-3).join(", "), n.boards.join(", ")].filter(Boolean).join(" · ");
  const answer = n.options.length
    ? h("div", "af-opts", n.options.map((o) => B({ class: "btn sm", title: `Answer “${o}” (fills it everywhere from now on)`,
        onclick: () => post("/api/autofill/needs/answer", { id: n.id, answer: o }, "Saved: it fills automatically from now on") }, o)))
    : (() => {
      const input = h("input", { type: "text", maxlength: "4000", placeholder: "Your answer", "aria-label": n.text });
      const save = () => input.value.trim() && post("/api/autofill/needs/answer", { id: n.id, answer: input.value }, "Saved: it fills automatically from now on");
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
      return h("div", "af-opts", input, B({ class: "btn sm primary", onclick: save }, "Save"));
    })();
  return h("li", "af-need",
    h("p", "af-q", n.text, n.legal ? h("span", { class: "tag warn", title: LEGAL_TIP }, "your answer only") : null),
    h("p", "hint", `${where}${n.seen > 1 ? ` · asked ${n.seen}×` : ""}${n.key_label ? ` · saves as “${n.key_label}”` : ""}`),
    answer,
    B({ class: "btn sm ghost", title: "Not worth keeping: stop asking", onclick: () => post("/api/autofill/needs/dismiss", { id: n.id }) }, "Don't ask again"));
}

// On / Off, as a checkbox (a missing or other value counts as On, like the server)
function toggle(row) {
  const box = h("input", { type: "checkbox", "aria-label": row.label });
  box.checked = row.answer !== "Off";
  const state = h("span", { class: "saved-state", role: "status" });
  box.addEventListener("change", async () => {
    state.textContent = "Saving…";
    try { const out = await api("/api/autofill/answer", { key: row.key, answer: box.checked ? "On" : "Off" }); row.answer = out.answer; state.textContent = `Saved ✓ (${out.answer})`; }
    catch (err) { box.checked = !box.checked; state.textContent = `Not saved: ${err.message}`; state.classList.add("err"); }
  });
  return h("label", "field set-field af-row af-toggle", h("span", "set-in", box, h("span", {}, row.label), state),
    h("span", "hint", "Off: your saved answer to the future-sponsorship question is used everywhere. “Do you need sponsorship now” always uses your saved answer."));
}

// a choice question is a dropdown (common answers + what boards showed), so a typo can't break matching
function choiceBox(row) {
  const known = row.choices.some((c) => c === row.answer);
  const sel = h("select", { "aria-label": row.label }, h("option", { value: "" }, "Not answered"),
    row.choices.map((c) => h("option", { value: c }, c)), row.answer && !known ? h("option", { value: row.answer }, `${row.answer} (your words)`) : null);
  sel.value = row.answer;
  return sel;
}

function field(row) {
  if (row.key === "sponsor_auto") return toggle(row);
  const input = row.key === "start_weeks" ? h("input", { type: "number", min: "0", max: "26", value: row.answer, "aria-label": row.label })
    : row.choices?.length ? choiceBox(row) : h(row.answer.length > 80 || row.category === "other" ? "textarea" : "input",
    { type: "text", maxlength: "4000", value: row.answer, "aria-label": row.label, rows: "2" });
  if (input.tagName === "TEXTAREA") input.value = row.answer;
  const state = h("span", { class: "saved-state", role: "status" });
  const empty = h("span", "hint af-empty", "Empty: a form asking this shows “Needs you”");
  empty.hidden = Boolean(row.answer) || row.blank_ok;
  const save = async () => {
    if (input.value === row.answer) return;
    state.textContent = "Saving…";
    state.classList.remove("err");
    try {
      const out = await api("/api/autofill/answer", { key: row.key, answer: input.value });
      row.answer = out.answer; input.value = out.answer; state.textContent = "Saved ✓";
      empty.hidden = Boolean(out.answer) || row.blank_ok;
    } catch (err) { state.textContent = `Not saved: ${err.message}`; state.classList.add("err"); }
  };
  input.addEventListener("change", save);
  const boards = Object.entries(row.options || {}).map(([b, t]) => `${b}: “${t}”`).join(" · ");
  const forget = row.key.startsWith("q:") ? B({ class: "btn sm ghost", title: "Remove this saved question", "aria-label": `Remove ${row.label}`, onclick: () => post("/api/autofill/forget", { key: row.key }, "Removed") }, "×") : null;
  return h("label", "field set-field af-row", h("span", {}, row.label, row.legal ? h("span", { class: "tag warn", title: LEGAL_TIP }, "your answer only") : null),
    h("span", "set-in", input, forget, state), boards ? h("span", "hint", `Exact wording: ${boards}`) : null, empty);
}

function addresses() {
  const home = data.home;
  const rows = data.addresses.map((a) => {
    const street = h("input", { type: "text", value: a.street, "aria-label": `Street for ${a.city_state}` });
    const zip = h("input", { type: "text", value: a.zip, maxlength: "5", inputmode: "numeric", "aria-label": `ZIP for ${a.city_state}` });
    return h("li", "af-addr", h("strong", {}, a.city_state), h("span", "af-line", a.line),
      h("span", "af-opts", street, zip,
        B({ class: "btn sm", onclick: () => post("/api/autofill/address/use", { city_state: a.city_state, street: street.value, zip: zip.value }, "Address saved") }, "Save"),
        B({ class: "btn sm ghost", onclick: () => post("/api/autofill/address/forget", { city_state: a.city_state }, "Removed") }, "Remove")));
  });
  return h("section", "af-sec", h("h2", {}, "Addresses"),
    h("p", "hint", "Forms get the address of the city on the resume used: your home for remote jobs and your own city, else the one saved for that city. " +
      "Street only, never an apartment number. New cities are suggested in the extension's panel (Use / Edit / Pick another)."),
    h("p", {}, h("strong", {}, "Home: "), home.kind === "home" ? home.line : h("span", "af-empty", "street and ZIP not saved yet (Contact, below)")),
    rows.length ? h("ul", "af-list", rows) : h("p", "hint", "No other city saved yet."));
}

function listEditor(key, title, cols, addLabel = "+ Add") {
  const bank = Object.fromEntries(data.bank.map((r) => [r.key, r]));
  let items = [];
  try { items = JSON.parse(bank[key]?.answer || "[]"); } catch { items = []; }
  const table = h("div", "af-hist");
  const draw = () => table.replaceChildren(...items.map((it, i) => h("div", "af-hrow", cols.map(([k, label]) => {
    if (k === "current") {
      const box = h("input", { type: "checkbox", "aria-label": label });
      box.checked = Boolean(it.current);
      box.addEventListener("change", () => { it.current = box.checked; if (box.checked) it.end = ""; });
      return h("label", "check", box, label);
    }
    const input = h("input", { type: "text", value: it[k] || "", placeholder: label, "aria-label": label });
    input.addEventListener("input", () => { it[k] = input.value; });
    return input;
  }), B({ class: "btn sm ghost", "aria-label": "Remove this row", onclick: () => { items.splice(i, 1); draw(); } }, "×"))));
  draw();
  return h("div", {}, title ? h("h3", {}, title) : null, table,
    h("div", "af-opts", B({ class: "btn sm ghost", onclick: () => { items.push({}); draw(); } }, addLabel),
      B({ class: "btn sm primary", onclick: () => post("/api/autofill/answer", { key, answer: JSON.stringify(items) }, "Saved") }, "Save")));
}

function history() {
  return h("section", "af-sec", h("h2", {}, "Work history"),
    h("p", "hint", "Workday's My Experience is corrected to this after it reads your resume. Dates MM/YYYY. From your base resume; add the city where you worked."),
    listEditor("history", "Jobs", [["company", "Company"], ["title", "Title"], ["location", "Location"], ["start", "From MM/YYYY"], ["end", "To MM/YYYY"], ["current", "I work here now"]]));
}

// 16:31: every degree with its start date; the first row is the highest (forms asking one degree get it)
const SINGLE_EDU = new Set(["degree", "field_of_study", "school", "grad_date"]);
function education(rows) {
  return h("section", "af-sec", h("h2", {}, "Education"),
    h("p", "hint", "Every degree, highest first, with its start and end (MM/YYYY). Workday's Education rows and any form asking your degree, school or graduation date get these."),
    listEditor("education_list", null, [["school", "School"], ["degree", "Degree"], ["field", "Field of study"], ["start", "Start MM/YYYY"], ["end", "End MM/YYYY"]], "+ Add a degree"),
    rows.length ? h("div", "set-group", rows.map(field)) : null);
}

function setup() {
  const ext = data.extension;
  return h("section", "af-sec", h("h2", {}, "The Autofill extension"),
    h("p", "hint", `Version ${ext.version}. Fills job applications with your tailored resume and these answers; never clicks Next or Submit. `,
      h("a", { href: ext.zip, download: "job-hunt-agent-autofill.zip" }, "Get the Autofill extension"), " (install steps in Settings › Autofill)."));
}

function render() {
  const cats = data.categories.filter(([c]) => c !== "history");
  const view = $("#view");
  view.replaceChildren(h("div", "af",
    h("header", "t-head", h("h1", {}, "Answers"), h("p", "muted small", "What the Autofill extension puts into job forms. Answer a question once: it fills everywhere after.")),
    h("section", "af-sec", h("h2", {}, `Needs you (${data.needs.length})`),
      data.needs.length ? h("ul", "af-list", data.needs.map(needCard)) : h("p", "hint", "Nothing waiting. Questions a form asks that have no saved answer show up here.")),
    typoNote(data.fixed), learned(data, post), addresses(),
    ...cats.map(([c, title]) => {
      const rows = data.bank.filter((r) => r.category === c && !(c === "education" && SINGLE_EDU.has(r.key)));
      if (c === "education") return education(rows);
      return rows.length ? h("section", "af-sec", h("h2", {}, title), h("div", "set-group", rows.map(field))) : null;
    }),
    companies(data, post), snippets(data, post), history(), stats(data.stats), setup()));
}

export async function loadAnswers() {
  css();
  if (data?.profile !== S.profile) data = null;
  const back = !!data;
  if (back) render();                                   // GOAL31 §3: the last answers at once, then fresh ones
  else { drawn = ""; $("#view").replaceChildren(placeholder()); }
  await reload(back);
}

// the top bar's counts, at start and on a person switch: Answers' Needs-you and the Apply queue
export async function refreshBadge() {
  import("./applyq.js").then((m) => m.refresh()).catch(() => {});
  try { badge((await get("/api/autofill/count")).open); } catch { /* the app answers it on the next open */ }
}

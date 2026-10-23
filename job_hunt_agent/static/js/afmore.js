// /§3: the Answers page's newer parts. Learned (what Autofill picked up from the user's own filling: edit, Remove =
// Undo), company-only answers (sponsorship, work authorization, legal, EEO changed on one company's form), saved long
// answers (offered in the panel, never filled by themselves), and Stats (how much Autofill filled by itself).
// Lazy, imported by answers.js.
import { B, api, h } from "./core.js";

const day = (at) => String(at || "").slice(0, 10);
const SCOPE = { global: "everywhere", company: "this company only" };

function editable(value, onsave, label) {
  const input = h("input", { type: "text", maxlength: "4000", value, "aria-label": label });
  const state = h("span", { class: "saved-state", role: "status" });
  input.addEventListener("change", async () => {
    state.textContent = "Saving…";
    state.classList.remove("err");
    try { await onsave(input.value); state.textContent = "Saved ✓"; } catch (err) { state.textContent = `Not saved: ${err.message}`; state.classList.add("err"); }
  });
  return [input, state];
}

export function typoNote(fixed) {
  if (!fixed?.length) return null;
  return h("p", "hint af-fixed", "Fixed so forms can match: ", fixed.map((f, i) => [i ? " · " : "", `${f.label}: “${f.was}” → “${f.now}”`]),
    " (Remove it under Learned to put your words back)");
}

export function learned(data, post) {
  const rows = data.learned || [];
  const item = (r) => {
    const company = () => data.overrides.find((o) => o.key === r.key && o.company === r.company)?.company_key || "";
    const save = (v) => (r.scope === "company" ? api("/api/autofill/override", { company: company(), key: r.key, answer: v })
      : api("/api/autofill/answer", { key: r.key, answer: v }));
    const [input, state] = editable(r.answer, save, `Answer to ${r.label}`);
    const where = [r.board, r.company, day(r.at)].filter(Boolean).join(" · ");
    const how = r.how === "override" ? "when you changed Autofill's answer" : r.how === "panel" ? "here" : "from your own filling";
    return h("li", "af-learn", h("p", "af-q", r.label, h("span", "tag", SCOPE[r.scope] || r.scope)),
      h("p", "hint", `Learned ${how} · ${where}`),
      h("div", "af-opts", input,
        B({ class: "btn sm ghost", title: "Undo: put back the answer it replaced", onclick: () => post("/api/autofill/learn/undo", { id: r.id }, "Removed: the earlier answer is back") }, "Remove"), state));
  };
  return h("section", "af-sec", h("h2", {}, `Learned (${rows.length})`),
    h("p", "hint", "When you fill or change a question yourself, Autofill keeps your answer for next time. Sponsorship, work authorization, legal and EEO answers are kept for that company only."),
    rows.length ? h("ul", "af-list", rows.map(item)) : h("p", "hint", "Nothing yet: it learns as you fill forms."));
}

export function companies(data, post) {
  const rows = data.overrides || [];
  const by = new Map();
  for (const r of rows) by.set(r.company, [...(by.get(r.company) || []), r]);
  const item = (r) => {
    const [input, state] = editable(r.answer, (v) => api("/api/autofill/override", { company: r.company_key, key: r.key, answer: v }), `${r.label} at ${r.company}`);
    return h("div", "af-row", h("span", {}, r.label), h("span", "set-in", input,
      B({ class: "btn sm ghost", "aria-label": `Remove ${r.label} for ${r.company}`, title: "Remove: this company gets your usual answer again", onclick: () => post("/api/autofill/override", { company: r.company_key, key: r.key, answer: "" }, "Removed") }, "×"), state));
  };
  return h("section", "af-sec", h("h2", {}, "Company answers"),
    h("p", "hint", "Answers you gave one company that differ from your usual ones (your usual answer is unchanged)."),
    by.size ? [...by].map(([company, list]) => h("div", "af-co", h("h3", {}, company), h("div", "set-group", list.map(item))))
      : h("p", "hint", "None yet."));
}

export function snippets(data, post) {
  const rows = data.snippets || [];
  const item = (s) => h("li", "af-need", h("p", "af-q", s.question), h("p", "hint", [s.company, day(s.at)].filter(Boolean).join(" · ")),
    h("p", "af-snip", s.text), B({ class: "btn sm ghost", onclick: () => post("/api/autofill/snippet/remove", { id: s.id }, "Removed") }, "Remove"));
  return h("section", "af-sec", h("h2", {}, `Saved long answers (${rows.length})`),
    h("p", "hint", "“Why us”, cover letters and other long answers you wrote. The panel offers them (Use a saved answer ▾); they are never filled on their own."),
    rows.length ? h("ul", "af-list", rows.map(item)) : h("p", "hint", "None yet."));
}

export function stats(s) {
  if (!s) return null;
  const pct = (n) => (n == null ? "?" : `${n}%`);
  const bar = (w) => h("div", "af-bar", h("span", "af-week", w.week), h("span", "af-track", h("span", { class: "af-fill", style: `width:${w.pct || 0}%` })),
    h("span", "af-pct", `${pct(w.pct)} auto · ${w.hand} by hand`));
  const app = (a) => h("tr", {}, h("td", {}, a.company || "(company)"), h("td", {}, a.title || ""), h("td", "num", String(a.auto)), h("td", "num", String(a.hand)), h("td", "num", pct(a.pct)));
  return h("section", "af-sec", h("h2", {}, "Stats"),
    h("p", {}, h("strong", {}, `${pct(s.overall.pct)} filled automatically`), ` (last 8 weeks: ${s.overall.auto} fields by Autofill, ${s.overall.hand} by hand)`),
    s.weeks.length ? h("div", { class: "af-bars", role: "list", "aria-label": "Share filled automatically, per week" }, s.weeks.map(bar)) : null,
    s.apps.length ? h("div", "af-tablewrap", h("table", "af-table", h("thead", {}, h("tr", {}, ["Company", "Role", "Autofill", "By hand", "Auto %"].map((t) => h("th", {}, t)))),
      h("tbody", {}, s.apps.map(app)))) : h("p", "hint", "No applications filled yet."));
}

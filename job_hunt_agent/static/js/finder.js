// Contacts: free signals, then Apollo / Hunter; drafts only.
// Lazy; keyed by tracker row id or feed uid.
import { B, btn, FIND_TIP, api, ask, copyText, extLink, fail, get, ghost, h, muted, toast } from "./core.js";

export const CONTACT_SOURCES = [["manual", "Manual"], ["jd", "JD"], ["team_page", "Team page"], ["apollo", "Apollo"],
  ["hunter", "Hunter"], ["linkedin-manual", "LinkedIn (manual)"], ["guess", "Guess"]];
export const SOURCE_TEXT = Object.fromEntries(CONTACT_SOURCES);
export const KIND_TEXT = { recruiter: "Recruiter", manager: "AI/ML hiring manager", peer: "Peer engineer" };
export const CONTACT_FIELDS = [["name", "Name"], ["role", "Role / title"], ["email", "Email"], ["linkedin", "LinkedIn URL"], ["notes", "Notes"]];

let openFor = null;

const small = (text) => h("p", "muted small", text);
const keyOf = (row) => (row.id != null ? { app: row.id } : { uid: row.uid });
const panelFor = (row, domain) => get("/api/contacts/panel", { ...keyOf(row), ...(domain ? { domain } : {}) });
const link = (url, text, title) => extLink(url, text, "small", title);

export function finderSection(row, rerender) {
  const body = h("div", "finder-body");
  const load = async () => {
    openFor = row.id;
    body.replaceChildren(small("Loading…"));
    try { renderFinder(body, row, await panelFor(row), rerender); } catch (err) { body.replaceChildren(); fail(err); }
  };
  if (openFor === row.id) queueMicrotask(load);
  return h("section", "d-sec finder", h("h3", {}, "Find contacts"),
    small("Free signals first: the JD, the company's team page, LinkedIn's own search pages. Apollo and Hunter spend " +
      "your own credits and only run when you click, with the cost shown first."),
    btn("primary", FIND_TIP, load, "Find contacts"), body);
}

export async function jobFinder(job, body, onRow) {
  const row = { id: job.app?.id, uid: job.uid };
  body.replaceChildren(small("Loading…"));
  try { renderFinder(body, row, await panelFor(row), onRow); } catch (err) { body.replaceChildren(); fail(err); }
}

const EMAIL_TIP = "How this email was found or checked";
function emailBits(s) {
  if (s.email) return [h("span", {}, s.email), h("span", { class: "tag", title: EMAIL_TIP }, s.email_status || "unverified")];
  if (s.email_guess) return [h("span", {}, s.email_guess), h("span", { class: "tag warn", title: EMAIL_TIP }, s.email_guess_status)];
  return [muted("no email")];
}

function suggestionCard(row, s, rerender) {
  const notes = [s.why.join("; "), s.email_guess ? `email ${s.email_guess} (${s.email_guess_status})` : ""].filter(Boolean).join(" · ");
  return h("div", "suggestion",
    h("div", "s-head", h("b", {}, s.name || "(no name)"), s.title ? h("span", {}, ` · ${s.title}`) : null,
      h("span", { class: "tag", title: "Likely role in the hiring" }, s.role),
      h("span", { class: `tag conf-${s.confidence}`, title: "How sure the ranking is" }, `${s.confidence} confidence`)),
    h("p", "small", muted("Why: "), s.why.join(" · ")),
    h("p", "small s-meta", muted("Email: "), ...emailBits(s),
      s.linkedin ? link(s.linkedin, "LinkedIn ↗", "Open this person's LinkedIn profile (new tab)") : null,
      muted(` · source: ${SOURCE_TEXT[s.source] || s.source}`)),
    s.name || s.email ? B({ class: "btn ghost", title: "Save this person to the job's tracker row (goes to your sheet once applied)",
      onclick: () => addContact(row, { name: s.name, role: s.title, email: s.email || "", linkedin: s.linkedin || "",
        source: s.source, kind: s.kind || "", notes }, rerender) }, "Add to contacts") : null);
}

function hunterAccount() {
  const line = h("p", { class: "small hunter-account", role: "status" }, "Hunter account: checking…");
  get("/api/contacts/hunter_account").then(({ account: a }) => {
    if (!a) { line.textContent = "Hunter account: no key"; return; }
    if (a.error) { line.textContent = `Hunter account: ${a.error}`; return; }
    line.textContent = `Hunter account: ${a.searches_left ?? "?"} searches · ${a.verifications_left ?? "?"} verifications left` +
      ` (${a.plan || "plan"} plan${a.reset_date ? `, resets ${a.reset_date}` : ""})`;
  }).catch((err) => { line.textContent = `Hunter account: ${err.message}`; });
  return line;
}

function addContactForm(row, rerender) {
  const inputs = Object.fromEntries(CONTACT_FIELDS.map(([f, label]) => [f, h("input", { name: f, placeholder: label, "aria-label": label,
    title: label, type: { email: "email", linkedin: "url" }[f] || "text" })]));
  const source = h("select", { name: "source", "aria-label": "Source", title: "Where this contact came from" },
    CONTACT_SOURCES.map(([v, t]) => h("option", { value: v }, t)));
  const form = h("form", "add-contact", Object.values(inputs), source,
    B({ type: "submit", class: "btn primary", title: "Save this contact to the job's tracker row" }, "Add contact"));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    addContact(row, { ...Object.fromEntries(CONTACT_FIELDS.map(([f]) => [f, inputs[f].value.trim()])), source: source.value }, rerender);
  });
  return h("div", {}, h("h4", {}, "Add contact"), form);
}

async function addContact(row, contact, rerender) {
  try {
    const out = await api("/api/contacts/add", { ...keyOf(row), contact });
    toast(`Added ${contact.name || contact.email}${row.id == null
      ? " · kept on a saved tracker row for this job (reaches your sheet once you apply)" : " · goes to your sheet's contact cells"}`);
    rerender(out);
  } catch (err) { fail(err); }
}

function domainRow(row, panel, body, rerender) {
  const domain = h("input", { name: "domain", id: "fd-domain", placeholder: "company.com", title: "Company domain the lookups use" });
  domain.value = panel.domain.value || "";
  const org = panel.org ? `${panel.org.name || ""}${panel.org.estimated_num_employees ? `, ~${panel.org.estimated_num_employees} employees` : ""}` : "";
  return h("div", "finder-domain", h("label", { for: "fd-domain", class: "muted small" }, "Company domain"), domain,
    btn("ghost", "Use this domain for the lookups", async () => {
      try { renderFinder(body, row, await panelFor(row, domain.value.trim()), rerender); } catch (err) { fail(err); }
    }, "Use"),
    panel.domain.source ? muted(` (${panel.domain.source})`, "muted small") : null, org ? muted(` · ${org}`, "muted small") : null);
}

function lookupBtn(text, title, ctx, provider, confirm, done) {
  const btn = B({ class: "btn", title, disabled: !ctx.panel.domain.value }, text);
  btn.onclick = async () => {
    if (confirm && !(await confirm())) return;
    btn.disabled = true;
    try {
      const out = await api("/api/contacts/lookup", { ...keyOf(ctx.row), provider, domain: ctx.panel.domain.value });
      done(out);
      renderFinder(ctx.body, ctx.row, out.panel, ctx.rerender);
    } catch (err) { fail(err); btn.disabled = false; }
  };
  return btn;
}

const plural = (n) => `credit${n === 1 ? "" : "s"}`;
function providerBox(ctx, name, label) {
  const p = ctx.panel.providers[name];
  if (!p.configured) return small(`${label}: ${p.note || "no key for you in config/secrets.yaml"}`);
  if (p.estimate.locked) return small(`${label}: ${p.estimate.detail}`);
  const cost = p.estimate.credits;
  const calls = cost > 0 || (p.estimate.kinds || []).length > 0;       // GOAL28: Apollo's search is a call at 0 credits
  const priced = cost ? `up to ${cost} ${plural(cost)}` : "0 credits";
  const run = lookupBtn(calls ? `Look up with ${label} (${priced})` : `Show ${label} results (free)`,
    calls ? `${label}: asks first, then spends ${priced} of your ${label} credits` : `${label}: nothing to ask now, shown free`,
    ctx, name, calls && (() => ask(cost ? `Spend up to ${cost} ${label} ${plural(cost)}?` : `Ask ${label}? It costs 0 credits`,
      `${ctx.panel.domain.value}: ${p.estimate.detail}. Today: ${p.used_today} of your cap ${p.cap} used.`, cost ? `Spend ${cost}` : `Ask ${label}`)),
    (out) => { const used = out.panel.providers[name];
      toast(`${label}: ${out.spent} ${plural(out.spent)} used · today ${used.used_today} of ${used.cap}${out.note ? ` · ${out.note}` : ""}`); });
  return h("div", "provider", run, name === "hunter" ? hunterAccount() : null,
    small(`${p.estimate.detail}. Credits spent through the app: today ${p.used_today} of your daily cap ${p.cap} ` +
      `(${p.left_today} left), this month ${p.used_month}.`),
    p.balance_note ? h("p", "muted small", `${p.balance_note} `, link(p.credits_page, "Your Apollo credits ↗", "Open your Apollo credits page (new tab)")) : null);
}

// GOAL28 §5: the three people worth reaching, never an empty card: who was found, else LinkedIn's own search for that
// kind; always the ready message for it (drafted only: copy it and send it yourself).
function kindCard(row, card, rerender) {
  const found = card.people.length;
  return h("div", { class: "kind-card", "data-kind": card.kind },
    h("b", {}, card.label, muted(found ? ` · ${found} found` : " · nobody found yet", "muted small")),
    found ? h("ul", "kind-people", card.people.map((s) => h("li", "small", h("b", {}, s.name), s.title ? ` · ${s.title}` : "",
      muted(` · ${SOURCE_TEXT[s.source] || s.source}`, "muted small"),
      B({ class: "btn ghost", title: `Save ${s.name} as the ${card.label.toLowerCase()} for this job`,
        onclick: () => addContact(row, { name: s.name, role: s.title, email: s.email || "", linkedin: s.linkedin || "",
          source: s.source, kind: card.kind, notes: s.why.join("; ") }, rerender) }, "Add"))))
      : h("p", "small", "Search LinkedIn: ", card.links.map((l) => link(l.url, `${l.label.replace("Find on LinkedIn: ", "")} ↗`,
        `${l.label}: LinkedIn's people search (new tab)`))),
    copyBox(`LinkedIn note (${card.draft.note.length}/300)`, card.draft.note, 3),
    h("details", "small", h("summary", { title: "Show the cold email and a prompt for your own Claude chat" },
      "Cold email and the prompt for your Claude chat"),
      copyBox("Subject", card.draft.subject, 1), copyBox(`Email (${card.draft.email.split(/\s+/).length} words)`, card.draft.email, 7),
      copyBox("Prompt for your Claude chat", card.draft.prompt, 8)));
}

function renderFinder(body, row, panel, rerender) {
  const ctx = { body, row, panel, rerender };
  const people = panel.suggestions.filter((s) => s.name || s.email);
  const hints = panel.suggestions.filter((s) => !s.name && !s.email);
  body.replaceChildren(...[
    domainRow(row, panel, body, rerender),
    h("h4", {}, "Who to contact"),
    small("Drafts only: Job Hunt Agent never sends anything. Copy a note, send it yourself, then add the person and mark it sent."),
    h("div", "kind-cards", (panel.kinds || []).map((card) => kindCard(row, card, rerender))),
    h("h4", {}, `Suggestions (${people.length})`),
    people.length ? h("div", "suggestions", people.map((s) => suggestionCard(row, s, rerender)))
      : small("Nobody named yet. Try the team page, LinkedIn or Apollo below."),
    hints.length ? h("p", "small", muted("From the JD: "),
      hints.map((x) => (x.email ? `${x.title} ${x.email}` : `reports to ${x.title}`)).join(" · ")) : null,
    h("h4", {}, "Search yourself (opens in a new tab)"),
    h("div", "finder-links", panel.links.linkedin.map((l) => link(l.url, `${l.label} ↗`, `${l.label}: a LinkedIn people search (new tab)`)),
      panel.links.apollo ? link(panel.links.apollo, "Open in Apollo (people at this domain) ↗", "Apollo's own people search for this domain (new tab)") : null),
    h("h4", {}, "Look up"),
    lookupBtn("Check the company's team page (free)", "Read the company's team page: free, no credits", ctx, "team_page", null,
      (out) => toast(out.note || "Checked")),
    providerBox(ctx, "apollo", "Apollo"), providerBox(ctx, "hunter", "Hunter"),
    addContactForm(row, rerender)].filter(Boolean));
}

function copyBox(label, text, rows) {
  const area = h("textarea", { readonly: true, rows: String(rows), "aria-label": label, title: label });
  area.value = text;
  return h("div", "copy-box", h("div", "copy-head", h("b", {}, label),
    btn("ghost", `Copy the ${label.toLowerCase()}`, () => copyText(text, `Copied: ${label}`), "Copy")), area);
}

function claudeBox(row, contact) {
  const box = h("div", "claude-box");
  const go = btn("", "One Claude call with your Anthropic key (Settings › Setup); about a cent", async () => {
    go.disabled = true;
    go.textContent = "Drafting…";
    try {
      const d = await api("/api/outreach/claude", { app: row.id, contact: contact.id });
      box.replaceChildren(small(`Drafted by ${d.by}`), copyBox("Subject", d.subject, 1), copyBox("Email", d.email, 8),
        copyBox(`LinkedIn note (${d.linkedin.length}/300 characters)`, d.linkedin, 3));
    } catch (err) { fail(err); go.disabled = false; go.textContent = "Draft with Claude"; }
  }, "Draft with Claude");
  box.append(go);
  return box;
}

export async function openOutreach(row, contact, rerender) {
  let d;
  try { d = await get("/api/outreach", { app: row.id, contact: contact.id }); } catch (err) { fail(err); return; }
  const dlg = h("dialog", { class: "form-dialog outreach-dialog", "aria-labelledby": "or-h" });
  const close = () => { if (dlg.open) dlg.close(); dlg.remove(); };
  const mark = (flag, label) => B({ class: "btn", disabled: !!d.contact[flag], title: `${label}: dated today in your sheet`, onclick: async () => {
    try {
      rerender(await api("/api/tracker/contact_flag", { contact: contact.id, flag, on: true }));
      toast(`${label}: dated today · in your sheet · follow-ups +3 / +6 days from today`);
      close();
    } catch (err) { fail(err); }
  } }, d.contact[flag] ? `${label} ${d.contact[flag]}` : `Mark ${label.toLowerCase()} today`);
  dlg.append(h("h2", { id: "or-h" }, `Draft outreach · ${contact.name || contact.email}`, muted(` · ${d.label || ""}`, "muted small")),
    small("the app never sends anything: copy, send it yourself, then mark it sent here (the date goes to your sheet's " +
      "Email Sent / LinkedIn Request cells)."),
    copyBox("Prompt for your Claude chat", d.prompt, 10),
    claudeBox(row, contact),
    h("h3", {}, "Plain template (works without Claude)"),
    copyBox("Subject", d.template.subject, 1), copyBox(`Email (${d.template.email.split(/\s+/).length} words)`, d.template.email, 8),
    copyBox(`LinkedIn note (${d.template.linkedin.length}/300 characters)`, d.template.linkedin, 3),
    h("div", "row-btns", mark("email_sent", "Email sent"), mark("linkedin_sent", "LinkedIn request sent"),
      btn("ghost", "Close (Esc)", close, "Close")));
  dlg.addEventListener("cancel", () => dlg.remove());
  document.body.append(dlg);
  dlg.showModal();
}

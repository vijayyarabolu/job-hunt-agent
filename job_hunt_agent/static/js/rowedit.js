// Row editor in the detail pane (lazy, via editor.js).
import { $, B, btn, FAMILY_LABELS, JOB_TABS, S, STATUS_LABELS, api, copyText, extLink, fail, fieldName, get, h,
  muted, shortDate, sm, toast, still } from "./core.js";
import { showPanel } from "./detail.js";
import { copyJd, loadFeed } from "./feed.js";
import { pickResumeFile } from "./dialogs.js";
import { EMPLOYMENT_OPTIONS, WORKPLACE_OPTIONS, paintRow, undoLastEdit } from "./inline.js";
import { CONTACT_FIELDS, SOURCE_TEXT, finderSection, openOutreach } from "./finder.js";
import { refreshFollowups } from "./editor.js";

const CONTACT_FLAGS = [["linkedin_sent", "LinkedIn sent"], ["email_sent", "Email sent"], ["replied", "Replied"]];
const NOTES = ["notes", "Notes", "textarea"];
const EXTRA_EDIT = [["job_url", "Job URL", "url"], ["apply_url", "Apply URL", "url"], ["location", "Location", "text"],
  ["workplace", "Workplace", "select", WORKPLACE_OPTIONS], ["salary", "Salary", "text"],
  ["employment_type", "Employment type", "select", EMPLOYMENT_OPTIONS], NOTES];
export const ADD_FIELDS = [["company", "Company Name", "text"], ["role_title", "Role Title", "text"], ...EXTRA_EDIT.slice(0, -1),
  ["date_applied", "Date Applied", "date"], ["resume", "Resume (file name or Docs/Drive link)", "text"], NOTES];
const EXTRA_SHOWN = ["resume_used", "match_pct", "source", "updated_at"];
const JD_STATE_TEXT = { saved: "saved", gone: "posting gone", no_url: "no posting link", unreadable: "not readable yet" };
const HISTORY_TEXT_MAX = 80;
let openRow = null;
let opener = null;
export const clearOpenRow = () => { openRow = null; };

const isUrl = (v) => /^https?:\/\//i.test(v || "");
const section = (title, ...kids) => h("section", "d-sec", h("h3", {}, title), ...kids);
export const note = (text) => h("p", "muted small", text);
export const statusSelect = (name, value) => {
  const sel = h("select", { name, id: name, title: "Pipeline status" }, Object.entries(STATUS_LABELS).map(([k, v]) => h("option", { value: k }, v)));
  sel.value = value;
  return sel;
};
export const labelled = (id, text, ...kids) => h("label", { class: "field", for: id }, h("span", {}, text), ...kids);

function fieldBox([field, label, kind, options], value, link) {
  const id = `f-${field.replace(/[^\w-]/g, "_")}`;
  const el = kind === "select" ? h("select", { name: field, id, title: label }, options.map(([k, v]) => h("option", { value: k }, v)))
    : kind === "textarea" ? h("textarea", { name: field, id, rows: "3", title: label })
      : h("input", { name: field, id, title: label, type: { date: "date", url: "url" }[kind] || "text" });
  el.value = value ?? "";
  return h("label", { class: `field${kind === "textarea" ? " wide" : ""}`, for: id }, h("span", {}, label), el,
    isUrl(link) ? extLink(link, "open ↗", "small", `Open ${label} ↗`) : null);
}

const dateOr = (spec, values) => (spec[2] === "date" ? values.applied_on || values[spec[0]] || "" : values[spec[0]]);
export const formGrid = (fields, values) => h("div", "form-grid", fields.map((f) =>
  fieldBox(f, dateOr(f, values), f[2] === "url" || f[0] === "resume" ? values[f[0]] : null)));
export const readFields = (form, fields) => Object.fromEntries(fields.map(([f]) => [f, (form.elements[f]?.value ?? "").trim()]));
const sheetKind = (row, field) => (field === "date_applied" ? "date" : String(row[field] ?? "").includes("\n") ? "textarea" : "text");

function sheetGrid(row) {
  return h("div", "form-grid", (row.sheet?.columns || []).map((c, i) => {
    if (!c.field) {
      return h("label", "field", h("span", {}, `${c.header} (kept from your sheet)`),
        h("input", { value: row.sheet.cells[i] || "", readonly: true, disabled: true, title: `${c.header}: kept from your sheet, edit it there` }));
    }
    const spec = [c.field, c.header, sheetKind(row, c.field)];
    return fieldBox(spec, dateOr(spec, row), c.field === "jd" && !isUrl(row.jd) ? row.job_url : row[c.field]);
  }));
}

export async function openRowEditor(appId) {
  openRow = appId;
  opener = document.activeElement;
  showPanel([h("p", "muted", "Loading…")]);
  try { renderEditor(await get("/api/tracker/row", { app: appId })); }
  catch (err) { showPanel([h("p", {}, `Could not load: ${err.message}`)]); }
}

export function refreshEditor(row) {
  if (row && openRow === row.id && S.detail?.panel && $("#detail .editor")) renderEditor(row, { keep: true });
}

function backTo(appId) {
  const el = opener?.isConnected ? opener : $(`#view [data-app="${appId}"] button`) || $(".t-wrap, #list");
  still(el);
}

function focusKey() {
  const act = document.activeElement;
  if (!$("#detail")?.contains(act)) return null;
  return act.name ? `[name="${act.name}"]` : act.id ? `#${act.id}` : act.title ? `[title="${act.title}"]` : null;
}

export function renderEditor(row, { keep = false } = {}) {
  openRow = row.id;
  const rerender = (fresh) => {
    const key = focusKey();
    renderEditor(fresh, { keep: true });
    if (key) still($(key, $("#detail")));
    paintRow(fresh, "saved");
    refreshFollowups();
    if (JOB_TABS.includes(S.tab)) loadFeed({ quiet: true });
  };
  const fus = [[row.fu1, 1], [row.fu2, 2]].filter(([fu]) => fu).map(([fu, n]) => followupLine(row, fu, n, rerender));
  showPanel([
    h("header", "d-head", h("h2", { id: "ed-h" }, `${row.company || "(company)"} · ${row.role_title || "(role)"}`),
      note(`Row #${row.id}${row.updated_at ? ` · updated ${shortDate(row.updated_at)}` : ""}` +
        `${row.family ? ` · ${FAMILY_LABELS[row.family] || row.family}` : ""}`)),
    fus.length ? section("Follow-ups", ...fus) : null,
    editorForm(row, rerender), jdSection(row), uploadsSection(row, rerender), contactsSection(row, rerender),
    finderSection(row, rerender), editHistory(row),
    section("Status history", h("ul", "small muted", (row.events || []).map((ev) =>
      h("li", {}, `${ev.at.slice(0, 10)} · ${ev.from_status || "new"} → ${ev.to_status} (${ev.note || ev.source}) `,
        ev.link && extLink(ev.link, "↗", 0, "Open the email")))))], { keep, onClose: () => backTo(row.id) });
}

function editorForm(row, rerender) {
  const sheetCols = (row.sheet?.columns || []).filter((c) => c.field).map((c) => [c.field]);
  const status = statusSelect("ed-status", row.status);
  const shown = EXTRA_SHOWN.map((k) => [k, (row.extras || {})[k]]).filter(([, v]) => v);
  const form = h("form", { class: "editor", autocomplete: "off", "aria-labelledby": "ed-h" },
    h("h3", {}, `Your sheet · ${row.sheet?.tab || ""}`),
    note("Same columns, same order as your sheet. Target name / role / LinkedIn / email cells come from the contacts below."),
    sheetGrid(row), h("h3", "grp-app-title", "App fields (not in your sheet)"),
    labelled("ed-status", "Pipeline status", status), formGrid(EXTRA_EDIT, row),
    shown.length ? note(shown.map(([k, v]) => `${fieldName(k)}: ${v}`).join(" · ")) : null,
    h("div", "row-btns sticky-save", B({ class: "btn primary", type: "submit", title: "Save (Enter)" }, "Save"),
      row.apply_url ? extLink(row.apply_url, "Apply page ↗", "btn ghost", "Open the apply page ↗") : null));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const was = (k) => (k === "date_applied" ? row.applied_on || "" : String(row[k] ?? "").trim());
    const changes = Object.fromEntries(Object.entries(readFields(form, [...sheetCols, ...EXTRA_EDIT])).filter(([k, v]) => v !== was(k)));
    if (status.value !== row.status) changes.status = status.value;
    if (!Object.keys(changes).length) { toast("Nothing changed"); return; }
    try {
      rerender(await api("/api/tracker/update", { app: row.id, changes }));
      toast(`Saved ${Object.keys(changes).map(fieldName).join(", ")}`, { undo: () => undoLastEdit(row.id) });
    } catch (err) { fail(err); }
  });
  return form;
}

export function followupLine(row, fu, n, onDone) {
  const when = { done: `done (${fu.done})`, overdue: `overdue since ${shortDate(fu.due)}`, today: "due today" }[fu.state]
    || `due ${shortDate(fu.due)}`;
  return h("div", `fu due-${fu.state}`, h("span", {}, `Follow-up ${n}: ${when}${fu.after && fu.after !== "applied" ? ` (after ${fu.after})` : ""}`),
    fu.state === "done" ? null : btn("", `Mark follow-up ${n} sent today`, async () => {
      try { onDone(await api("/api/tracker/followup", { app: row.id, n })); toast(`Follow-up ${n} marked sent today`); }
      catch (err) { fail(err); }
    }, sm("check"), "Mark sent today"));
}

function jdSection(row) {
  const snap = row.jd_snapshot;
  const url = snap?.url || row.job_url;
  const text = row.jd_text || snap?.text;
  return section("Job description",
    h("div", "row-btns", url ? extLink(url, "Original posting ↗", "btn ghost", "Open the original posting ↗") : muted("No posting link"),
      snap ? h("span", { class: `tag ${snap.state === "saved" ? "ok" : "warn"}`, title: snap.detail || `JD snapshot: ${JD_STATE_TEXT[snap.state] || snap.state}` }, JD_STATE_TEXT[snap.state] || snap.state) : null,
      text ? btn("ghost", "Copy the saved JD text", () => copyText(url ? `${url}\n\n${text}` : text, `Copied JD: ${row.company} · ${row.role_title}`),
        sm("copy"), "Copy JD") : null,
      row.job_uid ? btn("ghost", "Copy JD + keywords (c)", () => copyJd(row.job_uid, true),
        sm("copy"), "Copy JD + keywords") : null),
    snap?.source ? note(`Saved from ${snap.source}${snap.fetched_at ? ` on ${shortDate(snap.fetched_at)}` : ""}`) : null,
    text ? h("details", "jd-full", h("summary", { title: "Show or hide the saved JD text" }, "Show the saved JD"), h("div", "jd", text)) : note("No JD text saved for this row yet."));
}
function uploadsSection(row, rerender) {
  const list = (row.uploads || []).map((u) => h("li", {}, extLink(u.link, u.name, null, "Open this file in Drive ↗"),
    muted(` · v${u.version} · ${shortDate(u.uploaded_at)}${u.sheet ? ` · Sheet: ${u.sheet}` : ""}`)));
  return section("Resume files",
    note("Goes to your Google Drive (job-hunt-agent/Resumes) and becomes a link in your sheet's Resume cell. Old uploads stay."),
    row.resume_missing ? h("span", { class: "tag warn", title: "No resume file on this row yet" }, "resume missing") : null, list.length ? h("ul", "small", list) : null,
    btn("ghost", "Upload the resume you sent to Drive", () => pickResumeFile(row.id, rerender, row.has_resume && 1), "Upload resume (PDF/DOCX)"));
}

function flagBox(c, flag, label, rerender) {
  const box = h("input", { type: "checkbox", disabled: c.id === undefined, "aria-label": `${label}: ${c.name || "contact"}`, title: `${label} (stamps today)` });
  box.checked = !!c[flag];
  box.addEventListener("change", async () => {
    try {
      rerender(await api("/api/tracker/contact_flag", { contact: c.id, flag, on: box.checked }));
      toast(box.checked ? `${label}: dated today` : `${label}: cleared`);
    } catch (err) { box.checked = !box.checked; fail(err); }
  });
  return h("label", "check", box, h("span", {}, label), c[flag] ? muted(` ${c[flag]}`, "muted small") : null);
}

function contactBlock(c, i, row, rerender) {
  const inputs = CONTACT_FIELDS.map(([f, label]) => {
    const input = h("input", { name: `c${i}.${f}`, "aria-label": label, placeholder: label, title: label, type: { email: "email", linkedin: "url" }[f] || "text" });
    input.value = c[f] || "";
    return input;
  });
  return h("div", { class: "contact", "data-i": String(i) }, h("div", "contact-fields", inputs),
    h("div", "contact-flags", CONTACT_FLAGS.map(([flag, label]) => flagBox(c, flag, label, rerender)),
      c.linkedin ? extLink(c.linkedin, "LinkedIn ↗", "small", "Open their LinkedIn profile ↗") : null,
      c.email ? h("a", { href: `mailto:${c.email}`, class: "small", title: `Email ${c.email}` }, "Email") : null,
      c.source ? muted(`source: ${SOURCE_TEXT[c.source] || c.source}`, "muted small") : null,
      c.id !== undefined ? btn("ghost", "Draft outreach to this person", () => openOutreach(row, c, rerender), "Draft outreach") : null,
      B({ class: "btn ghost", "aria-label": `Remove ${c.name || "this contact"} (then Save contacts)`, title: "Remove this contact (then Save contacts)",
        onclick: (e) => e.currentTarget.closest(".contact").remove() }, "Remove")));
}

function readContacts(box, current) {
  return [...box.querySelectorAll(".contact")].map((node) => {
    const base = current[Number(node.dataset.i)] || {};
    const get = (f) => node.querySelector(`[name="c${node.dataset.i}.${f}"]`).value.trim();
    return { ...Object.fromEntries(CONTACT_FIELDS.map(([f]) => [f, get(f)])), source: base.source || "",
      ...Object.fromEntries(CONTACT_FLAGS.map(([f]) => [f, base[f] || ""])) };
  });
}

function contactsSection(row, rerender) {
  const contacts = row.contacts || [];
  const box = h("div", "contacts", contacts.map((c, i) => contactBlock(c, i, row, rerender)));
  let next = contacts.length;
  return section("Contacts",
    note("Several people per application. Tick LinkedIn sent / Email sent / Replied per person (ticking stamps today's date)."),
    box, h("div", "row-btns",
      btn("ghost", "Add another contact", () => box.append(contactBlock({}, next++, row, rerender)), sm("plus"), "Add contact"),
      btn("primary", "Save every contact on this row", async () => {
        try {
          rerender(await api("/api/tracker/contacts", { app: row.id, contacts: readContacts(box, contacts) }));
          toast("Contacts saved", { undo: () => undoLastEdit(row.id) });
        } catch (err) { fail(err); }
      }, "Save contacts")));
}

function shorten(v) {
  if (v == null || v === "") return "(empty)";
  const text = String(v).replace(/\s+/g, " ");
  return text.length > HISTORY_TEXT_MAX ? `${text.slice(0, HISTORY_TEXT_MAX - 1)}…` : text;
}

function editHistory(row) {
  const items = (row.history || []).map((x) => h("li", {},
    `${x.at.replace("T", " ").slice(0, 16)} · ${x.who} · ${fieldName(x.field)}: `,
    muted(`${shorten(x.old_value)} → ${shorten(x.new_value)}`), x.undone_by ? muted(" (undone)") : null));
  return section("Edit history", items.length ? h("ul", "small history", items) : note("No edits yet."),
    btn("ghost", "Undo the last edit on this row (u)", () => undoLastEdit(row.id), sm("undo"), "Undo last edit on this row"));
}

// Lazy (via editor.js): Follow-ups, Add a job, Vendors.
import { $, $$, B, btn, OPEN_TIP, S, api, ask, fail, get, ghost, h, isoToday, muted, shortDate, sm, toast, still } from "./core.js";
import { showPanel } from "./detail.js";
import { reloadTracker, setFollowupCount } from "./editor.js";
import { ADD_FIELDS, clearOpenRow, followupLine, formGrid, labelled, note, openRowEditor, readFields, renderEditor,
  statusSelect } from "./rowedit.js";

const VENDOR_FIELDS = [["company", "Vendor Companies"], ["target_name", "Target name"], ["target_role", "Target role"],
  ["linkedin", "Linkedin connection"], ["email", "Mail"], ["email_sent", "Mail Sent"], ["notes", "Notes"]];
const BUCKETS = [["Overdue", "overdue", "due-overdue"], ["Due today", "today", "due-today"], ["Next 3 days", "upcoming", "due-upcoming"]];

function followupItem(row) {
  const who = (row.contacts || []).filter((c) => c.name || c.email).map((c) => c.name || c.email).join(", ");
  const li = h("li", { class: "fu-row", "data-app": String(row.id) },
    h("div", "fu-head", B({ class: "linklike", title: OPEN_TIP, onclick: () => openRowEditor(row.id) }, h("b", {}, row.company), ` · ${row.role_title}`),
      muted(`applied ${shortDate(row.applied_on)}`, "muted small")),
    who ? h("p", "small", `Contacts: ${who}`) : null,
    [[row.fu1, 1], [row.fu2, 2]].filter(([fu]) => fu && fu.state !== "done").map(([fu, n]) => followupLine(row, fu, n, (fresh) => settle(li, fresh))));
  return li;
}

function settle(li, fresh) {
  const next = [fresh.fu1, fresh.fu2].find((fu) => fu && fu.state !== "done");
  const bucket = next && BUCKETS.find(([, state]) => state === next.state);
  li.remove();
  if (bucket) {
    const grp = $(`.fu-group.${bucket[2]}`);
    const list = grp.querySelector(".fu-list") || h("ul", "fu-list");
    grp.querySelector("p.small")?.remove();
    grp.append(list);
    list.append(followupItem(fresh));
    still(list.lastChild.querySelector("button"));
  } else still($(".fu-view h1"));
  paintFollowupCounts();
}

function paintFollowupCounts() {
  for (const g of $$(".fu-group[data-bucket]")) {
    const n = g.querySelectorAll(".fu-row").length;
    g.querySelector("h2 .muted").textContent = String(n);
    if (!n) g.querySelector(".fu-list")?.replaceWith(note("Nothing here."));
  }
  const due = $$(".fu-group.due-overdue .fu-row, .fu-group.due-today .fu-row").length;
  $(".fu-due").textContent = `${due} due now · follow-up 1 at 3 days, follow-up 2 at 6 days after applying`;
  setFollowupCount(due);
}

const group = (title, rows, cls) => h("section", { class: `fu-group ${cls}`, "data-bucket": cls },
  h("h2", {}, `${title} `, muted(String(rows.length))), rows.length ? h("ul", "fu-list", rows.map(followupItem)) : note("Nothing here."));

export async function loadFollowups() {
  const view = $("#view");
  if (!$(".fu-view")) view.replaceChildren(h("p", "pad muted", "Loading…"));
  try {
    const d = await get("/api/tracker/followups");
    if (S.tab !== "followups") return;
    view.replaceChildren(h("div", "fu-view", h("header", "t-head", h("h1", { tabindex: "-1" }, "Follow-ups"), h("p", "muted small fu-due")),
      S.g8?.followupsTop(), BUCKETS.map(([title, key, cls]) => group(title, d[key], cls))));
    paintFollowupCounts();
  } catch (err) { view.replaceChildren(h("p", "pad", `Could not load: ${err.message}`)); }
}

export function openAddJob() {
  clearOpenRow();
  const url = h("input", { type: "url", name: "url", id: "add-url", placeholder: "Paste the job link", title: "Paste the job posting's link" });
  const msg = h("p", { class: "muted small", role: "status" },
    "Greenhouse, Lever, Ashby, Workday and SmartRecruiters links fill in by themselves; any other link is saved with what you type.");
  const status = statusSelect("add-status", "applied");
  const formBox = h("div");
  let jobUid = null;
  const drawFields = (fields) => formBox.replaceChildren(formGrid(ADD_FIELDS, { ...fields, applied_on: isoToday() }),
    labelled("add-status", "Status", status));
  drawFields({});
  const fill = btn("", "Fill the fields from the posting", async () => {
    msg.textContent = "Reading the posting…";
    msg.classList.remove("bad");
    try {
      const found = await get("/api/tracker/lookup", { url: url.value.trim() });
      jobUid = found.job_uid || null;
      drawFields(found.fields);
      msg.textContent = found.error || (found.supported ? `Filled from ${found.from === "feed" ? "the job feed" : found.from}. Check it, then save.`
        : "Not an ATS link we can read: type the company, role and the rest.");
      msg.classList.toggle("bad", !!found.error);
    } catch (err) { msg.textContent = err.message; msg.classList.add("bad"); }
  }, "Fill from link");
  const form = h("form", { class: "editor", autocomplete: "off", "aria-labelledby": "add-h" },
    labelled("add-url", "Job link"), h("div", "url-row", url, fill), msg, formBox,
    h("div", "row-btns sticky-save", B({ class: "btn primary", type: "submit", title: "Save as a new tracker row" }, "Save to tracker")));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = { ...readFields(form, ADD_FIELDS), status: status.value, job_uid: jobUid };
    if (!data.job_url && url.value.trim()) data.job_url = url.value.trim();
    try {
      const row = await api("/api/tracker/add", { data });
      toast(`Added: ${row.company} · ${row.role_title}`);
      renderEditor(row);
      reloadTracker();
    } catch (err) { fail(err); }
  });
  showPanel([h("h2", { id: "add-h" }, "Add a job by link"), form]);
  url.focus();
}

function vendorRow(v) {
  return h("div", "contact vendor", h("div", "contact-fields", VENDOR_FIELDS.map(([f, label]) => {
    const input = h("input", { name: f, placeholder: label, "aria-label": label, title: label });
    input.value = v[f] || "";
    return input;
  })), btn("ghost", "Remove this vendor (then Save vendors)", (e) => e.currentTarget.closest(".vendor").remove(), "Remove"));
}

export async function openVendors() {
  clearOpenRow();
  showPanel([h("p", "muted", "Loading…")]);
  let vendors;
  try { ({ vendors } = await get("/api/vendors")); }
  catch (err) { showPanel([h("p", {}, `Could not load: ${err.message}`)]); return; }
  const box = h("div", "contacts", vendors.map(vendorRow));
  const saveAll = async () => {
    const rows = [...box.querySelectorAll(".vendor")].map((n) =>
      Object.fromEntries(VENDOR_FIELDS.map(([f]) => [f, n.querySelector(`[name="${f}"]`).value.trim()])));
    const clear = !rows.some((r) => Object.values(r).some(Boolean));
    if (clear && !(await ask("Delete every vendor?", "The vendors list (and your sheet's vendors tab) will be empty.", "Delete all"))) return;
    try { await api("/api/vendors", { vendors: rows, clear }); toast("Vendors saved (syncs to your sheet)"); openVendors(); }
    catch (err) { fail(err); }
  };
  const add = ghost(() => box.append(vendorRow({})), sm("plus"), "Add vendor");
  add.title = "Add another vendor";
  showPanel([h("h2", {}, "Vendors / recruiters"), note("Staffing vendors and recruiters you work with (not tied to one application)."),
    box, h("div", "row-btns sticky-save", add, btn("primary", "Save every vendor (syncs to your sheet)", saveAll, "Save vendors"))]);
}

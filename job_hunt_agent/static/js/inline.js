// Cell editing: Enter saves, Esc cancels, ✓ / ✕ on phones.
import { B, EMPLOYMENTS, WORKPLACES, api, extLink, fail, fieldName, get, h, optimistic, toast } from "./core.js";

const SYNC_POLL_MS = 4000;
const SYNC_POLL_MAX = 30;
const CELL_MAX = 60;
const LONG_FIELDS = ["jd", "notes"];
export const WORKPLACE_OPTIONS = [["", "—"], ...WORKPLACES];
export const EMPLOYMENT_OPTIONS = [["", "—"], ...EMPLOYMENTS, ["part_time", "Part-time"], ["internship", "Internship"]];
const SELECTS = { workplace: WORKPLACE_OPTIONS, employment_type: EMPLOYMENT_OPTIONS };
const URL_RX = /https?:\/\/[^\s<>"']+/;
export const EDIT_TIP = "Edit this cell (Enter saves, Esc cancels)";
const hooks = { redraw: () => {}, badge: () => {}, editor: () => {}, patch: (row, field, value) => ({ ...row, [field]: value }) };
export const setInlineHooks = (given) => Object.assign(hooks, given);
export const paintRow = (row, badge) => hooks.redraw(row, badge);

const cut = (t) => (t.length > CELL_MAX ? `${t.slice(0, CELL_MAX - 1)}…` : t);

export function cellDisplay(value, link) {
  const box = h("span", "cell-text");
  if (value == null || value === "") return box;
  const text = String(value);
  const found = text.match(URL_RX);
  const url = found ? found[0].replace(/[.,;:!?)\]}'"]+$/, "") : (/^https?:\/\//i.test(link || "") ? link : null);
  if (url) {
    const a = extLink(url, "↗", "cell-link");
    a.title = `Open ${url}`;
    a.addEventListener("click", (e) => e.stopPropagation());
    box.append(a, " ");
  }
  box.append(cut(found ? (text.replace(url, "").trim() || url) : text));
  if (text.length > CELL_MAX) box.title = text.slice(0, 2000);
  return box;
}

function input(field, value) {
  const name = fieldName(field);
  if (SELECTS[field]) {
    const sel = h("select", { "aria-label": name, title: `${name} (Enter saves, Esc cancels)` }, SELECTS[field].map(([k, v]) => h("option", { value: k }, v)));
    sel.value = value || "";
    return [sel, "select"];
  }
  const long = LONG_FIELDS.includes(field) || String(value ?? "").includes("\n");
  const el = long ? h("textarea", { rows: "6", "aria-label": name, title: `${name} (Enter saves, Shift+Enter: new line, Esc cancels)` })
    : h("input", { type: "text", "aria-label": name, title: `${name} (Enter saves, Esc cancels)`, placeholder: field === "date_applied" ? "MM/DD/YYYY" : null });
  el.value = value ?? "";
  return [el, long ? "textarea" : "text"];
}

export function makeEditable(td, edit) {
  td.classList.add("editable");
  td.tabIndex = 0;
  td.title ||= EDIT_TIP;
  const start = async () => {
    if (td.classList.contains("editing")) return;
    td.classList.add("editing");
    let value = typeof edit.value === "function" ? edit.value() : edit.value;
    if (edit.full) {
      try { value = await edit.full(); } catch (err) { td.classList.remove("editing"); return fail(err); }
    }
    const [el, kind] = input(edit.field, value);
    const before = [...td.childNodes];
    const cancel = () => { td.classList.remove("editing"); td.replaceChildren(...before); td.focus(); };
    const commit = async () => {
      const next = el.value.trim();
      if (next === String(value ?? "").trim()) return cancel();
      el.disabled = true;
      try { await edit.save(next); } catch (err) { el.disabled = false; fail(err); el.focus(); }
    };
    el.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Escape") { e.preventDefault(); cancel(); }
      if (e.key === "Enter" && !(kind === "textarea" && e.shiftKey)) { e.preventDefault(); commit(); }
    });
    td.replaceChildren(el, h("span", "cell-acts",
      B({ class: "cell-btn", "aria-label": "Save (Enter)", title: "Save (Enter)", onclick: (e) => { e.stopPropagation(); commit(); } }, "✓"),
      B({ class: "cell-btn", "aria-label": "Cancel (Esc)", title: "Cancel (Esc)", onclick: (e) => { e.stopPropagation(); cancel(); } }, "✕")));
    el.focus();
    if (kind === "text") el.select();
  };
  td.addEventListener("click", (e) => { if (!e.target.closest("a, button")) start(); });
  td.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === td) { e.preventDefault(); start(); } });
}

export function saveTrackerCell(row, field, value) {
  return optimistic(() => hooks.redraw(hooks.patch(row, field, value)), () => hooks.redraw(row),
    async () => {
      const fresh = await api("/api/tracker/update", { app: row.id, changes: { [field]: value } });
      hooks.redraw(fresh, "saved");
      watchSync(fresh.id);
      return fresh;
    }, `Saved ${fieldName(field)}`, () => undoLastEdit(row.id));
}

export async function watchSync(appId) {
  for (let i = 0; i < SYNC_POLL_MAX; i++) {
    await new Promise((r) => setTimeout(r, SYNC_POLL_MS));
    if (document.hidden) continue;
    let s;
    try { s = await get("/api/tracker/sync", { app: appId }); } catch { continue; }
    const errors = Object.values(s.errors || {});
    if (errors.length) return hooks.badge(appId, "failed", errors.join("; "));
    if (s.state === "synced") return hooks.badge(appId, "synced", `in your Google Sheet (synced ${s.synced_at})`);
    if (s.state === "not_on_sheet") return hooks.badge(appId, "offsheet", s.why);
  }
}

export function syncBadge(state, detail) {
  const text = { saved: "saved ✓", synced: "saved ✓ · synced ✓", failed: "sync failed", offsheet: "saved ✓" }[state];
  return h("span", { class: `sync-badge s-${state}`, title: detail || "Saved in the app", role: "status" }, text);
}

export async function undoLastEdit(appId) {
  try {
    const out = await api("/api/tracker/undo", appId ? { app: appId } : {});
    toast(`Undone: ${out.undone.map((u) => fieldName(u.field)).join(", ")}`);
    hooks.redraw(out.row, "saved");
    hooks.editor(out.row);
    watchSync(out.row.id);
  } catch (err) { fail(err); }
}
